#!/usr/bin/env python3
"""POST 測試：重現 infiniteslop.ai /api/vote.php 投票請求。"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://infiniteslop.ai/api/vote.php"
DEFAULT_SOURCE_URL = "https://studio.404hubs.com/latest"
DEFAULT_ID = 65432
DEFAULT_TIMES = 10
SEEN_PATH = Path(__file__).resolve().with_name(".vote_source_seen")


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
    args = parser.parse_args()

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

    key = source_key(vote_id, times)
    if used_source and not args.force and load_seen() == key:
        print(f"Skip: {key} already processed")
        return 0

    exit_code = 0
    for i in range(1, times + 1):
        cid = args.cid if args.cid else random_cid()
        label = f"[{i}/{times}]"
        code = post_vote(args.url, vote_id, cid, args.timeout, label=label)
        if code != 0:
            exit_code = code
        if i < times:
            print()
    if used_source and exit_code == 0:
        save_seen(key)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
