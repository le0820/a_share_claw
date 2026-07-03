from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def slug(value: str, default: str = "item") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_.-")
    return cleaned or default


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_datetime(value: str, tz_name: str) -> datetime:
    raw = value.strip()
    if not raw:
        raise ValueError("empty datetime")
    normalized = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                dt = None  # type: ignore[assignment]
        if dt is None:
            raise
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(timezone.utc)


def parse_interval_seconds(value: str | int | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = value.strip().lower()
    if text.isdigit():
        seconds = int(text)
        return seconds if seconds > 0 else None
    fixed = {
        "hourly": 3600,
        "daily": 86400,
        "weekly": 7 * 86400,
        "每小时": 3600,
        "每天": 86400,
        "每日": 86400,
        "每周": 7 * 86400,
    }
    if text in fixed:
        return fixed[text]
    match = re.fullmatch(r"every\s+(\d+)\s+(minute|minutes|hour|hours|day|days|week|weeks)", text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        scale = 60 if unit.startswith("minute") else 3600 if unit.startswith("hour") else 86400
        if unit.startswith("week"):
            scale = 7 * 86400
        return amount * scale
    match = re.fullmatch(r"每\s*(\d+)\s*(分钟|小时|天|周)", text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        scale = 60 if unit == "分钟" else 3600 if unit == "小时" else 86400
        if unit == "周":
            scale = 7 * 86400
        return amount * scale
    raise ValueError(f"unsupported interval: {value}")


def next_run_after(now: datetime, interval_seconds: int) -> datetime:
    return now + timedelta(seconds=interval_seconds)


def safe_join(root: Path, user_path: str) -> Path:
    base = root.resolve()
    target = (base / user_path).resolve() if not os.path.isabs(user_path) else Path(user_path).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"path escapes workspace: {user_path}")
    return target


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40] + "\n...[truncated]..."


def split_message(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts
