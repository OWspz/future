#!/usr/bin/env python3
"""POST 測試：重現 infiniteslop.ai /api/vote.php 投票請求。"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://infiniteslop.ai/api/vote.php"
DEFAULT_SOURCE_URL = "https://studio.404hubs.com/latest"
DEFAULT_WS_URL = "wss://studio.404hubs.com/ws"
DEFAULT_WS_NAME = "vote_post_test"
DEFAULT_ID = 65432
DEFAULT_TIMES = 10
SEEN_PATH = Path(__file__).resolve().with_name(".vote_source_seen")
JOB_TEXT_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")


def random_cid() -> str:
    """Generate a 32-char hex cid, matching the original payload format."""
    return uuid.uuid4().hex


def fetch_source(source_url: str, timeout: float) -> tuple[int, int]:
    """Read `id,times` from a text endpoint such as studio.404hubs.com/latest."""
    request = urllib.request.Request(
        source_url,
        headers={
            "Accept": "text/plain,*/*",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace").strip()
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 2:
        raise ValueError(f"expected 'id,times', got: {text!r}")
    return int(parts[0]), int(parts[1])


def source_key(vote_id: int, times: int) -> str:
    return f"{vote_id},{times}"


def load_seen() -> str:
    if not SEEN_PATH.is_file():
        return ""
    return SEEN_PATH.read_text(encoding="utf-8").strip()


def save_seen(key: str) -> None:
    SEEN_PATH.write_text(key + "\n", encoding="utf-8")


def parse_job_text(text: str) -> tuple[int, int] | None:
    match = JOB_TEXT_RE.match(text or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def run_batch(
    api_url: str,
    vote_id: int,
    times: int,
    timeout: float,
    cid: str | None = None,
    skip_if_seen: bool = False,
    force: bool = False,
) -> int:
    key = source_key(vote_id, times)
    if skip_if_seen and not force and load_seen() == key:
        print(f"Skip: {key} already processed")
        return 0

    exit_code = 0
    for i in range(1, times + 1):
        next_cid = cid if cid else random_cid()
        label = f"[{i}/{times}]"
        code = post_vote(api_url, vote_id, next_cid, timeout, label=label)
        if code != 0:
            exit_code = code
        if i < times:
            print()
    if skip_if_seen and exit_code == 0:
        save_seen(key)
    return exit_code


async def reply_ok(ws, name: str) -> None:
    await ws.send(json.dumps({"name": name, "text": "OK"}, ensure_ascii=False))
    print("WS reply: OK")


async def handle_ws_text(
    ws,
    payload: dict,
    api_url: str,
    timeout: float,
    ws_name: str,
    seen_message_ids: set[str],
) -> None:
    message_id = str(payload.get("id") or "")
    if message_id:
        if message_id in seen_message_ids:
            return
        seen_message_ids.add(message_id)

    sender = str(payload.get("name") or "")
    text = str(payload.get("text") or "")
    if sender == ws_name or text.strip().upper() == "OK":
        return

    job = parse_job_text(text)
    if job is None:
        return

    vote_id, times = job
    print(f"WS job from {sender}: {vote_id},{times}")
    if times < 1:
        print("Skip: times must be >= 1")
        return
    code = run_batch(
        api_url,
        vote_id,
        times,
        timeout,
        skip_if_seen=True,
    )
    if code == 0:
        await reply_ok(ws, ws_name)


async def listen_ws(ws_url: str, ws_name: str, api_url: str, timeout: float) -> int:
    try:
        import websockets
    except ImportError:
        print("websockets is required: pip install websockets")
        return 1

    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Origin": "https://studio.404hubs.com",
    }
    print(f"WS connect {ws_url} as {ws_name}")
    async with websockets.connect(ws_url, additional_headers=headers) as ws:
        seen_message_ids: set[str] = set()
        async for raw in ws:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                print(f"WS non-json: {raw}")
                continue

            if not isinstance(data, dict):
                continue

            kind = data.get("type")
            if kind == "hello":
                for item in data.get("messages") or []:
                    if isinstance(item, dict) and item.get("id"):
                        seen_message_ids.add(str(item["id"]))
                print(
                    f"WS hello: skip {len(seen_message_ids)} history, "
                    f"online={data.get('online')}"
                )
                continue
            if kind == "online":
                print(f"WS online={data.get('online')}")
                continue

            message = data
            if kind == "message" and isinstance(data.get("message"), dict):
                message = data["message"]
            if "text" in message:
                await handle_ws_text(
                    ws, message, api_url, timeout, ws_name, seen_message_ids
                )
    return 0


HEADERS = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Origin": "https://infiniteslop.ai",
    "Referer": "https://infiniteslop.ai/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
    "Accept-Language": (
        "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7,"
        "zh-CN;q=0.6,id;q=0.5,th;q=0.4,ja;q=0.3"
    ),
}


def post_vote(
    url: str, vote_id: int, cid: str, timeout: float, label: str = ""
) -> int:
    payload = {"id": vote_id, "cid": cid}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers=HEADERS,
        method="POST",
    )

    if label:
        print(label)
    print(f"POST {url}")
    print(f"Payload: {json.dumps(payload, ensure_ascii=False)}")
    print("-" * 40)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        print(f"HTTP {status}")
        if content_type:
            print(f"Content-Type: {content_type}")
        print(raw.decode("utf-8", errors="replace"))
        return 1
    except urllib.error.URLError as exc:
        print(f"Request failed: {exc.reason}")
        return 1

    text = raw.decode("utf-8", errors="replace")
    print(f"HTTP {status}")
    if content_type:
        print(f"Content-Type: {content_type}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print(text)
        return 1

    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("-" * 40)
    print(
        "votes={votes}  counted={counted}  self={self}".format(
            votes=data.get("votes"),
            counted=data.get("counted"),
            self=data.get("self"),
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="POST test for infiniteslop.ai /api/vote.php"
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="API URL")
    parser.add_argument(
        "--from-url",
        nargs="?",
        const=DEFAULT_SOURCE_URL,
        default=None,
        help=(
            "read id,times from a text URL "
            f"(default if flag only: {DEFAULT_SOURCE_URL})"
        ),
    )
    parser.add_argument("--id", type=int, default=None, help="vote id")
    parser.add_argument(
        "--cid",
        default=None,
        help="client id (32-char hex); omit to generate a new random value each time",
    )
    parser.add_argument(
        "--times",
        type=int,
        default=None,
        help="how many POST requests to send",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="timeout seconds")
    parser.add_argument(
        "--force",
        action="store_true",
        help="run even if this id,times was already processed from --from-url",
    )
    parser.add_argument(
        "--ws",
        nargs="?",
        const=DEFAULT_WS_URL,
        default=None,
        help=f"listen on WebSocket (default: {DEFAULT_WS_URL})",
    )
    parser.add_argument(
        "--ws-name",
        default=DEFAULT_WS_NAME,
        help=f"name sent with OK replies (default: {DEFAULT_WS_NAME})",
    )
    args = parser.parse_args()

    if args.ws:
        try:
            return asyncio.run(
                listen_ws(args.ws, args.ws_name, args.url, args.timeout)
            )
        except KeyboardInterrupt:
            print("WS stopped")
            return 0

    vote_id = args.id
    times = args.times
    used_source = False
    if args.from_url:
        try:
            fetched_id, fetched_times = fetch_source(args.from_url, args.timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            print(f"Failed to read {args.from_url}: {exc}")
            return 1
        print(f"Source {args.from_url} -> id={fetched_id} times={fetched_times}")
        if vote_id is None:
            vote_id = fetched_id
        if times is None:
            times = fetched_times
        used_source = True
    if vote_id is None:
        vote_id = DEFAULT_ID
    if times is None:
        times = DEFAULT_TIMES
    if times < 1:
        print("--times must be >= 1")
        return 1

    return run_batch(
        args.url,
        vote_id,
        times,
        args.timeout,
        cid=args.cid,
        skip_if_seen=used_source,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
