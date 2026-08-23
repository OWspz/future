#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Long-running MQTT monitor for A/B-station Bingo draws.

Subscribes to both stations, detects missing period numbers, stale draws,
disconnects and bad payloads, then emails alerts.

  python3 mqtt_bingo_monitor.py --config mqtt_bingo_monitor.json
  python3 mqtt_bingo_monitor.py --check
  python3 mqtt_bingo_monitor.py --self-test
"""

import argparse
import json
import logging
import os
import signal
import smtplib
import socket
import ssl
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from logging.handlers import RotatingFileHandler

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.stderr.write("Please install paho-mqtt: pip3 install paho-mqtt\n")
    sys.exit(1)

TZ_TW = timezone(timedelta(hours=8))
DRAW_INTERVAL = timedelta(minutes=5)
FIRST_DRAW = (7, 5)
LAST_DRAW = (23, 55)
BALL_COUNT = 20
HISTORY_COUNT = 220
ALERT_COOLDOWN_SEC = 30 * 60
KEEPALIVE = 60

DEFAULT_SITES = [
    {
        "name": "A\u7ad9",
        "host": "mqtt.kuaishou1688.com",
        "port": 1883,
        "tls": False,
        "websocket": False,
    },
    {
        "name": "B\u7ad9",
        "host": "mqtt.sport.kuaishou1688.com",
        "port": 1883,
        "tls": False,
        "websocket": False,
    },
]

DEFAULT_CONFIG = {
    "alert_email": "asus4951024@gmail.com",
    "smtp": {
        "host": "smtp.gmail.com",
        "port": 587,
        "user": "",
        "password": "",
        "from_addr": "",
        "starttls": True,
        "ssl": False,
    },
    "sites": DEFAULT_SITES,
    "history_count": HISTORY_COUNT,
    "stale_grace_sec": 90,
    "alert_cooldown_sec": ALERT_COOLDOWN_SEC,
    "state_file": "mqtt_bingo_monitor.state.json",
    "log_file": "mqtt_bingo_monitor.log",
}


def now_tw():
    return datetime.now(TZ_TW)


def parse_draw_dt(item):
    date_s = str(item.get("\u958b\u734e\u65e5\u671f") or "").strip()
    time_s = str(item.get("\u958b\u734e\u6642\u9593") or "").strip()
    if not date_s or not time_s:
        return None
    if len(time_s) == 5:
        time_s += ":00"
    try:
        dt = datetime.strptime(date_s + " " + time_s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=TZ_TW)


def is_operating(dt=None):
    dt = dt or now_tw()
    minutes = dt.hour * 60 + dt.minute
    start = FIRST_DRAW[0] * 60 + FIRST_DRAW[1]
    end = LAST_DRAW[0] * 60 + LAST_DRAW[1]
    return start <= minutes <= end + 6


def next_expected_draw(last_dt):
    if last_dt is None:
        return None
    last_min = LAST_DRAW[0] * 60 + LAST_DRAW[1]
    if last_dt.hour * 60 + last_dt.minute >= last_min:
        nxt = last_dt + timedelta(days=1)
        return nxt.replace(
            hour=FIRST_DRAW[0],
            minute=FIRST_DRAW[1],
            second=0,
            microsecond=0,
        )
    return last_dt + DRAW_INTERVAL


def find_missing_terms(terms):
    nums = sorted({int(t) for t in terms})
    missing = []
    for a, b in zip(nums, nums[1:]):
        if b > a + 1:
            missing.extend(range(a + 1, b))
    return missing


def summarize_terms(terms, limit=12):
    nums = sorted({int(t) for t in terms})
    if not nums:
        return "(\u7121)"
    if len(nums) <= limit:
        return ", ".join(str(n) for n in nums)
    head = ", ".join(str(n) for n in nums[:limit])
    return "%s \u2026 %s\uff08\u5171 %d \u671f\uff09" % (head, nums[-1], len(nums))


def term_of(item):
    value = item.get("\u671f\u6578")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_draw(item):
    errors = []
    if term_of(item) is None:
        errors.append("\u7f3a\u5c11\u6216\u7121\u6548\u671f\u6578")
    balls = item.get("\u4e00\u822c\u734e\u865f")
    nums = []
    if not isinstance(balls, list) or len(balls) != BALL_COUNT:
        errors.append("\u4e00\u822c\u734e\u865f\u6578\u91cf\u4e0d\u662f %d" % BALL_COUNT)
    else:
        try:
            nums = [int(x) for x in balls]
        except (TypeError, ValueError):
            errors.append("\u4e00\u822c\u734e\u865f\u542b\u975e\u6578\u5b57")
            nums = []
        if nums and len(set(nums)) != len(nums):
            errors.append("\u4e00\u822c\u734e\u865f\u6709\u91cd\u8907")
        if nums and (min(nums) < 1 or max(nums) > 80):
            errors.append("\u4e00\u822c\u734e\u865f\u8d85\u51fa 1-80")
    super_ball = item.get("\u8d85\u7d1a\u734e\u865f")
    if super_ball in (None, ""):
        errors.append("\u7f3a\u5c11\u8d85\u7d1a\u734e\u865f")
    elif isinstance(balls, list):
        super_s = str(super_ball).zfill(2)
        ball_s = [str(x).zfill(2) for x in balls]
        if super_s not in ball_s:
            errors.append("\u8d85\u7d1a\u734e\u865f\u4e0d\u5728\u4e00\u822c\u734e\u865f\u5167")
    if parse_draw_dt(item) is None:
        errors.append("\u958b\u734e\u65e5\u671f\u6216\u6642\u9593\u683c\u5f0f\u932f\u8aa4")
    return errors


def deep_update(base, extra):
    for key, val in extra.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], val)
        else:
            base[key] = val


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
        deep_update(cfg, user)
    env_mail = os.environ.get("BINGO_ALERT_EMAIL")
    if env_mail:
        cfg["alert_email"] = env_mail
    smtp = cfg.setdefault("smtp", {})
    env_user = os.environ.get("BINGO_SMTP_USER")
    env_pass = os.environ.get("BINGO_SMTP_PASSWORD")
    if env_user:
        smtp["user"] = env_user
    if env_pass:
        smtp["password"] = env_pass
    return cfg


def make_mqtt_client(client_id, site):
    transport = "websockets" if site.get("websocket") else "tcp"
    kwargs = {
        "client_id": client_id,
        "protocol": mqtt.MQTTv311,
        "transport": transport,
        "clean_session": True,
    }
    callback_api = getattr(mqtt, "CallbackAPIVersion", None)
    if callback_api is not None:
        try:
            client = mqtt.Client(callback_api.VERSION1, **kwargs)
        except TypeError:
            client = mqtt.Client(**kwargs)
    else:
        client = mqtt.Client(**kwargs)
    if site.get("websocket"):
        client.ws_set_options(path=site.get("ws_path") or "/mqtt")
    if site.get("tls"):
        ctx = ssl.create_default_context()
        if site.get("tls_insecure"):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(ctx)
    return client


class Mailer(object):
    def __init__(self, cfg, dry_run=False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.lock = threading.Lock()

    def enabled(self):
        smtp = self.cfg.get("smtp") or {}
        return bool(
            self.cfg.get("alert_email")
            and smtp.get("host")
            and smtp.get("user")
            and smtp.get("password")
        )

    def send(self, subject, body):
        to_addr = self.cfg.get("alert_email")
        smtp = self.cfg.get("smtp") or {}
        from_addr = (
            smtp.get("from_addr")
            or smtp.get("user")
            or to_addr
            or "bingo-monitor@localhost"
        )
        text = body.strip() + "\n"
        logging.info("ALERT %s", subject)
        logging.info("%s", text)
        if self.dry_run:
            logging.info("dry-run: skip email")
            return True
        if not self.enabled():
            logging.warning("SMTP \u672a\u8a2d\u5b9a\u5e33\u5bc6\uff0c\u8b66\u5831\u53ea\u5beb\u5165 log")
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg.set_content(text, charset="utf-8")
        host = smtp.get("host") or "smtp.gmail.com"
        port = int(smtp.get("port") or 587)
        user = smtp.get("user") or ""
        password = smtp.get("password") or ""
        try:
            with self.lock:
                if smtp.get("ssl"):
                    server = smtplib.SMTP_SSL(host, port, timeout=30)
                else:
                    server = smtplib.SMTP(host, port, timeout=30)
                with server:
                    server.ehlo()
                    if smtp.get("starttls", True) and not smtp.get("ssl"):
                        server.starttls()
                        server.ehlo()
                    server.login(user, password)
                    server.send_message(msg)
            logging.info("\u5df2\u5bc4\u51fa email \u81f3 %s", to_addr)
            return True
        except Exception as exc:
            logging.exception("\u5bc4\u4fe1\u5931\u6557: %s", exc)
            return False


class SharedAlerts(object):
    def __init__(self, cfg, mailer, state_file):
        self.cfg = cfg
        self.mailer = mailer
        self.state_file = state_file
        self.lock = threading.Lock()
        self.last_sent = {}
        self.latest = {}
        self._load()

    def _load(self):
        if not self.state_file or not os.path.isfile(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.last_sent = data.get("last_sent") or {}
            self.latest = data.get("latest") or {}
        except Exception as exc:
            logging.warning("\u8b80\u53d6\u72c0\u614b\u6a94\u5931\u6557: %s", exc)

    def _save(self):
        if not self.state_file:
            return
        payload = {
            "last_sent": self.last_sent,
            "latest": self.latest,
            "saved_at": now_tw().isoformat(),
        }
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_file)
        except Exception as exc:
            logging.warning("\u5beb\u5165\u72c0\u614b\u6a94\u5931\u6557: %s", exc)

    def note_latest(self, site, term):
        if term is None:
            return
        with self.lock:
            self.latest[site] = term
            self._save()

    def check_divergence(self):
        with self.lock:
            items = [
                (key, val)
                for key, val in self.latest.items()
                if val is not None
            ]
        if len(items) < 2:
            return
        terms = [item[1] for item in items]
        if max(terms) - min(terms) < 1:
            return
        detail = "\u3001".join("%s=%s" % (name, term) for name, term in items)
        self.alert(
            "A/B",
            "diverge",
            detail,
            "[Bingo] A/B \u7ad9\u671f\u6578\u4e0d\u4e00\u81f4",
            "\u5169\u7ad9\u6700\u65b0\u671f\u6578\u4e0d\u540c\uff1a%s\n\u6642\u9593: %s"
            % (detail, now_tw().strftime("%Y-%m-%d %H:%M:%S")),
        )

    def alert(self, site, kind, fingerprint, subject, body, cooldown=True):
        key = "%s|%s|%s" % (site, kind, fingerprint)
        cooldown_sec = int(
            self.cfg.get("alert_cooldown_sec") or ALERT_COOLDOWN_SEC
        )
        now = time.time()
        with self.lock:
            last = float(self.last_sent.get(key) or 0)
            if cooldown and now - last < cooldown_sec:
                logging.info("cooldown skip %s", key)
                return
            self.last_sent[key] = now
            self._save()
        self.mailer.send(subject, body)


class SiteMonitor(object):
    def __init__(self, site, cfg, mailer, shared):
        self.site = site
        self.name = site["name"]
        self.cfg = cfg
        self.mailer = mailer
        self.shared = shared
        self.client_id = "bingo_monitor_%s_%s" % (
            self.name.replace("\u7ad9", ""),
            uuid.uuid4().hex[:8],
        )
        self.client = None
        self.connected = False
        self.terms = {}
        self.latest_term = None
        self.latest_item = None
        self.latest_update = None
        self.last_message_at = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.disconnect_since = None

    def start(self):
        self.client = make_mqtt_client(self.client_id, self.site)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=2, max_delay=60)
        host = self.site["host"]
        port = int(self.site.get("port") or 1883)
        logging.info(
            "[%s] \u9023\u7dda %s:%s client=%s",
            self.name,
            host,
            port,
            self.client_id,
        )
        self.client.connect_async(host, port, keepalive=KEEPALIVE)
        self.client.loop_start()

    def stop(self):
        self.stop_event.set()
        if self.client is None:
            return
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        ok = (rc == 0) if isinstance(rc, int) else (str(rc) == "Success")
        self.connected = bool(ok)
        logging.info("[%s] MQTT connected rc=%s", self.name, rc)
        if not ok:
            self.shared.alert(
                self.name,
                "connect_fail",
                str(rc),
                "[%s Bingo] MQTT \u9023\u7dda\u5931\u6557" % self.name,
                "\u7ad9\u9ede: %s\nbroker: %s:%s\nrc: %s\n\u6642\u9593: %s"
                % (
                    self.name,
                    self.site["host"],
                    self.site.get("port"),
                    rc,
                    now_tw().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            return
        if self.disconnect_since:
            down = now_tw() - self.disconnect_since
            self.disconnect_since = None
            if down >= timedelta(seconds=20):
                self.shared.alert(
                    self.name,
                    "reconnect",
                    "ok",
                    "[%s Bingo] MQTT \u5df2\u6062\u5fa9\u9023\u7dda" % self.name,
                    "\u7ad9\u9ede: %s \u5df2\u91cd\u65b0\u9023\u4e0a MQTT\uff08\u4e2d\u65b7\u7d04 %s\uff09\u3002\n\u6642\u9593: %s"
                    % (
                        self.name,
                        str(down).split(".")[0],
                        now_tw().strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                    cooldown=False,
                )
        client.subscribe("bingo/data_update", qos=1)
        client.subscribe(
            "bingo/client_response/%s" % self.client_id,
            qos=1,
        )
        self._request_history()

    def _on_disconnect(self, client, userdata, rc, properties=None):
        self.connected = False
        if self.disconnect_since is None:
            self.disconnect_since = now_tw()
        logging.warning("[%s] MQTT disconnected rc=%s", self.name, rc)
        if self.stop_event.is_set():
            return
        self.shared.alert(
            self.name,
            "disconnect",
            "down",
            "[%s Bingo] MQTT \u9023\u7dda\u4e2d\u65b7" % self.name,
            "\u7ad9\u9ede: %s\nbroker: %s:%s\nrc: %s\n\u6642\u9593: %s\n\u7a0b\u5f0f\u6703\u81ea\u52d5\u91cd\u9023\u3002"
            % (
                self.name,
                self.site["host"],
                self.site.get("port"),
                rc,
                now_tw().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

    def _request_history(self):
        count = int(self.cfg.get("history_count") or HISTORY_COUNT)
        payload = json.dumps(
            {
                "type": "request_latest_data",
                "client_id": self.client_id,
                "count": count,
                "timestamp": now_tw().isoformat(),
            },
            ensure_ascii=False,
        )
        self.client.publish("bingo/client_request", payload, qos=0)
        logging.info("[%s] \u5df2\u8acb\u6c42\u6700\u8fd1 %d \u671f", self.name, count)

    def _on_message(self, client, userdata, msg):
        self.last_message_at = now_tw()
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            self.shared.alert(
                self.name,
                "bad_json",
                msg.topic,
                "[%s Bingo] MQTT \u8a0a\u606f\u4e0d\u662f JSON" % self.name,
                "\u7ad9\u9ede: %s\ntopic: %s\n\u932f\u8aa4: %s\n\u5167\u5bb9\u524d 400 \u5b57:\n%s"
                % (
                    self.name,
                    msg.topic,
                    exc,
                    msg.payload[:400].decode("utf-8", "replace"),
                ),
            )
            return
        topic = msg.topic
        if topic == "bingo/data_update":
            self._handle_data_update(data)
        elif topic.startswith("bingo/client_response/"):
            self._handle_history(data)

    def _handle_data_update(self, data):
        if data.get("success") is False:
            self.shared.alert(
                self.name,
                "update_fail",
                str(data.get("type")),
                "[%s Bingo] \u958b\u734e\u66f4\u65b0\u5931\u6557" % self.name,
                "\u7ad9\u9ede: %s\npayload:\n%s"
                % (self.name, json.dumps(data, ensure_ascii=False)[:2000]),
            )
            return
        items = data.get("data") or []
        if isinstance(items, dict):
            items = [items]
        self._ingest(items, source="data_update")
        latest = data.get("latest_term")
        if latest is not None:
            try:
                latest = int(latest)
            except (TypeError, ValueError):
                latest = None
        with self.lock:
            if latest is not None:
                if (
                    self.latest_term is not None
                    and latest > self.latest_term + 1
                ):
                    missing = list(range(self.latest_term + 1, latest))
                    self._alert_missing(missing, "data_update.latest_term")
                self.latest_term = max(self.latest_term or 0, latest)
            self.latest_update = data.get("update_time")
        self.shared.note_latest(self.name, self.latest_term)

    def _handle_history(self, data):
        if data.get("success") is False:
            logging.warning("[%s] history \u5931\u6557: %s", self.name, data)
            return
        items = data.get("data") or []
        self._ingest(items, source="history")
        with self.lock:
            terms = list(self.terms.keys())
        missing = find_missing_terms(terms)
        if missing:
            self._alert_missing(missing, "history")

    def _ingest(self, items, source):
        new_terms = []
        for item in items:
            if not isinstance(item, dict):
                continue
            errs = validate_draw(item)
            term = term_of(item)
            if errs:
                self.shared.alert(
                    self.name,
                    "bad_draw",
                    str(term),
                    "[%s Bingo] \u958b\u734e\u8cc7\u6599\u7570\u5e38" % self.name,
                    "\u7ad9\u9ede: %s\n\u4f86\u6e90: %s\n\u671f\u6578: %s\n\u932f\u8aa4: %s\n\u8cc7\u6599: %s"
                    % (
                        self.name,
                        source,
                        term,
                        "\u3001".join(errs),
                        json.dumps(item, ensure_ascii=False),
                    ),
                )
            if term is None:
                continue
            with self.lock:
                is_new = term not in self.terms
                self.terms[term] = item
                if self.latest_term is None or term > self.latest_term:
                    prev = self.latest_term
                    self.latest_term = term
                    self.latest_item = item
                    if prev is not None and term > prev + 1:
                        missing = list(range(prev + 1, term))
                        self._alert_missing(missing, source)
            if is_new:
                new_terms.append(term)
        if new_terms:
            logging.info(
                "[%s] %s \u65b0\u671f\u6578 %s (\u6700\u65b0 %s)",
                self.name,
                source,
                summarize_terms(new_terms),
                self.latest_term,
            )
            self.shared.note_latest(self.name, self.latest_term)

    def _alert_missing(self, missing, source):
        if not missing:
            return
        with self.lock:
            known = set(self.terms.keys())
        still = [term for term in missing if term not in known]
        if not still:
            return
        self.shared.alert(
            self.name,
            "missing_term",
            "%s-%s" % (still[0], still[-1]),
            "[%s Bingo] \u7f3a\u671f\u6578 %s"
            % (self.name, summarize_terms(still, limit=8)),
            "\u7ad9\u9ede: %s\n\u4f86\u6e90: %s\n\u7f3a\u5c11\u671f\u6578: %s\n\u76ee\u524d\u6700\u65b0\u671f\u6578: %s\n\u6642\u9593: %s"
            % (
                self.name,
                source,
                summarize_terms(still, limit=40),
                self.latest_term,
                now_tw().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

    def check_stale(self):
        if not is_operating():
            return
        with self.lock:
            item = self.latest_item
            connected = self.connected
            last_msg = self.last_message_at
        if not connected:
            return
        last_dt = parse_draw_dt(item) if item else None
        expected = next_expected_draw(last_dt)
        grace = timedelta(seconds=int(self.cfg.get("stale_grace_sec") or 90))
        now = now_tw()
        if expected and now > expected + grace:
            last_txt = (
                last_dt.strftime("%Y-%m-%d %H:%M") if last_dt else "\u672a\u77e5"
            )
            self.shared.alert(
                self.name,
                "stale",
                str(self.latest_term),
                "[%s Bingo] \u958b\u734e\u903e\u6642\uff08\u53ef\u80fd\u7f3a\u671f\uff09" % self.name,
                "\u7ad9\u9ede: %s\n\u6700\u65b0\u671f\u6578: %s\n\u6700\u65b0\u958b\u734e\u6642\u9593: %s\n"
                "\u9810\u671f\u4e0b\u4e00\u671f: %s\n\u73fe\u5728: %s\n\u5df2\u8d85\u904e %s \u4ecd\u672a\u6536\u5230\u65b0\u671f\u3002"
                % (
                    self.name,
                    self.latest_term,
                    last_txt,
                    expected.strftime("%Y-%m-%d %H:%M"),
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    str(now - expected).split(".")[0],
                ),
            )
        elif last_msg and now - last_msg > timedelta(minutes=3):
            self.shared.alert(
                self.name,
                "silent",
                "no-msg",
                "[%s Bingo] MQTT \u7121\u8a0a\u606f" % self.name,
                "\u7ad9\u9ede: %s \u5df2 %s \u6c92\u6709\u6536\u5230\u4efb\u4f55 MQTT \u8a0a\u606f\u3002\n\u6642\u9593: %s"
                % (
                    self.name,
                    str(now - last_msg).split(".")[0],
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )


def setup_logging(log_file):
    root = logging.getLogger()
    root.handlers[:] = []
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)
    if log_file:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(fmt)
        root.addHandler(handler)


def run_self_test():
    missing = find_missing_terms([10, 11, 13, 16])
    assert missing == [12, 14, 15], missing
    item = {
        "\u671f\u6578": 115047688,
        "\u958b\u734e\u65e5\u671f": "2026-08-23",
        "\u958b\u734e\u6642\u9593": "22:30",
        "\u4e00\u822c\u734e\u865f": [
            "04", "09", "12", "18", "22", "27", "29", "31",
            "32", "39", "42", "46", "50", "58", "62", "66",
            "67", "71", "73", "80",
        ],
        "\u8d85\u7d1a\u734e\u865f": "32",
        "\u5927\u5c0f": "\uff0d",
        "\u55ae\u96d9": "\uff0d",
    }
    assert validate_draw(item) == []
    bad = dict(item)
    bad["\u4e00\u822c\u734e\u865f"] = bad["\u4e00\u822c\u734e\u865f"][:19]
    assert validate_draw(bad)
    last = datetime(2026, 8, 23, 23, 55, tzinfo=TZ_TW)
    nxt = next_expected_draw(last)
    assert nxt.hour == 7 and nxt.minute == 5, nxt
    print("self-test ok")
    return 0


def run_check(cfg):
    setup_logging(None)
    results = []
    for site in cfg["sites"]:
        cid = "bingo_monitor_check_%s" % uuid.uuid4().hex[:8]
        client = make_mqtt_client(cid, site)
        box = {
            "connected": False,
            "items": [],
            "err": None,
            "latest": None,
        }

        def on_connect(c, u, f, rc, properties=None, _box=box):
            ok = (rc == 0) if isinstance(rc, int) else (str(rc) == "Success")
            _box["connected"] = bool(ok)
            if not ok:
                _box["err"] = "rc=%s" % rc
                return
            c.subscribe("bingo/data_update")
            c.subscribe("bingo/client_response/%s" % cid)
            c.publish(
                "bingo/client_request",
                json.dumps(
                    {
                        "type": "request_latest_data",
                        "client_id": cid,
                        "count": 30,
                        "timestamp": now_tw().isoformat(),
                    },
                    ensure_ascii=False,
                ),
            )

        def on_message(c, u, msg, _box=box):
            data = json.loads(msg.payload.decode("utf-8"))
            if data.get("latest_term") is not None:
                _box["latest"] = data.get("latest_term")
            for item in data.get("data") or []:
                term = term_of(item)
                if term is not None:
                    _box["items"].append(term)
                    if _box["latest"] is None:
                        _box["latest"] = term

        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(site["host"], int(site.get("port") or 1883), 30)
        except Exception as exc:
            box["err"] = str(exc)
        else:
            t0 = time.time()
            while time.time() - t0 < 8:
                client.loop(0.2)
            try:
                client.disconnect()
            except Exception:
                pass
        missing = find_missing_terms(box["items"])
        row = {
            "site": site["name"],
            "host": site["host"],
            "connected": box["connected"],
            "latest_term": box["latest"],
            "history": len(set(box["items"])),
            "missing": missing,
            "error": box["err"],
        }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False))
    ok = results and all(
        r["connected"] and not r["missing"] and not r["error"]
        for r in results
    )
    return 0 if ok else 1


def run_monitor(cfg, dry_run=False):
    setup_logging(cfg.get("log_file"))
    logging.info("Bingo MQTT monitor start host=%s", socket.gethostname())
    mailer = Mailer(cfg, dry_run=dry_run)
    if not mailer.enabled() and not dry_run:
        logging.warning(
            "\u5c1a\u672a\u8a2d\u5b9a SMTP \u5e33\u5bc6\uff1a\u8b66\u5831\u53ea\u6703\u5beb log\u3002"
            "\u8acb\u5728\u8a2d\u5b9a\u6a94\u586b smtp.user / smtp.password\uff0c"
            "\u6216\u7528\u74b0\u5883\u8b8a\u6578 BINGO_SMTP_USER\u3001BINGO_SMTP_PASSWORD\u3002"
            "\u6536\u4ef6\u4eba\u76ee\u524d\u70ba %s",
            cfg.get("alert_email"),
        )
    shared = SharedAlerts(cfg, mailer, cfg.get("state_file"))
    monitors = [
        SiteMonitor(site, cfg, mailer, shared) for site in cfg["sites"]
    ]
    stop = threading.Event()

    def handle_stop(signum, frame):
        logging.info("\u6536\u5230\u8a0a\u865f %s\uff0c\u6e96\u5099\u7d50\u675f", signum)
        stop.set()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    for mon in monitors:
        mon.start()
    while not stop.is_set():
        stop.wait(20)
        for mon in monitors:
            mon.check_stale()
        shared.check_divergence()
    for mon in monitors:
        mon.stop()
    logging.info("Bingo MQTT monitor stopped")
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Long-running A/B Bingo MQTT monitor with email alerts",
    )
    parser.add_argument(
        "--config",
        default="mqtt_bingo_monitor.json",
        help="config file path",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="connect both sites, fetch terms, then exit",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run missing-term logic tests only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log alerts without sending email",
    )
    parser.add_argument(
        "--write-example-config",
        metavar="PATH",
        help="write example config file",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    if args.self_test:
        return run_self_test()
    if args.write_example_config:
        with open(args.write_example_config, "w", encoding="utf-8") as fh:
            json.dump(DEFAULT_CONFIG, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print("wrote", args.write_example_config)
        return 0
    cfg_path = args.config if os.path.isfile(args.config) else None
    cfg = load_config(cfg_path)
    if args.check:
        return run_check(cfg)
    return run_monitor(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
