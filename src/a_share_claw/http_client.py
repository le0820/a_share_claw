from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_HEADERS = {
    "User-Agent": "a-share-claw/0.1 (+https://github.com/yifan/a_share_claw)",
    "Accept": "text/html,application/json,text/plain,*/*",
}


async def fetch_text(url: str, timeout: float = 15.0, max_bytes: int = 2_000_000) -> str:
    return await asyncio.to_thread(_fetch_text_sync, url, timeout, max_bytes)


async def fetch_json(url: str, timeout: float = 15.0, max_bytes: int = 2_000_000) -> Any:
    text = await fetch_text(url, timeout=timeout, max_bytes=max_bytes)
    return json.loads(text)


async def post_json(url: str, payload: dict[str, Any], timeout: float = 30.0) -> Any:
    return await asyncio.to_thread(_post_json_sync, url, payload, timeout)


def _fetch_text_sync(url: str, timeout: float, max_bytes: int) -> str:
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
            encoding = response.headers.get_content_charset() or "utf-8"
            return raw.decode(encoding, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body}") from exc


def _post_json_sync(url: str, payload: dict[str, Any], timeout: float) -> Any:
    data = urllib.parse.urlencode({k: v for k, v in payload.items() if v is not None}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=DEFAULT_HEADERS | {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            encoding = response.headers.get_content_charset() or "utf-8"
            return json.loads(raw.decode(encoding, errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body}") from exc
