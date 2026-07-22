"""Shared date, provenance, and fallback checks for pipeline artifacts."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


FALLBACK_STATUSES = {"none", "stale_fallback", "static_fallback", "unverified"}
DATE_FIELDS = {
    "effective_date",
    "filing_date",
    "last_trade_date_used",
    "observation_date",
    "publication_date",
    "release_date",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def embedded_date(data: dict[str, Any]) -> str | None:
    meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
    return data.get("as_of_date") or data.get("data_date") or data.get("_date") or meta.get("as_of_date")


def fallback_status(data: dict[str, Any]) -> str:
    meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
    return str(meta.get("fallback_status", data.get("fallback_status", "none")))


def contains_static_marker(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source" and isinstance(item, str):
                source = item.strip().casefold()
                if source.startswith(("fallback", "static", "estimated", "hardcoded")):
                    return True
            if contains_static_marker(item):
                return True
    elif isinstance(value, list):
        return any(contains_static_marker(item) for item in value)
    return False


def parse_loose_date(value: str):
    normalized = value.strip()
    for fmt, candidate in (
        ("%Y-%m-%d", normalized[:10]),
        ("%Y%m%d", normalized[:8]),
        ("%Y-%m", normalized[:7]),
        ("%Y%m", normalized[:6]),
    ):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def future_date_fields(value: Any, as_of_date: str, path: str = "payload") -> list[str]:
    as_of = datetime.strptime(as_of_date, "%Y%m%d").date()
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in DATE_FIELDS and isinstance(item, str):
                parsed = parse_loose_date(item)
                if parsed is not None and parsed > as_of:
                    found.append(f"{child}={item}")
            found.extend(future_date_fields(item, as_of_date, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(future_date_fields(item, as_of_date, f"{path}[{index}]"))
    return found


def validate_payload(
    data: dict[str, Any],
    requested_date: str,
    *,
    allow_stale: bool = False,
    allow_static: bool = False,
    label: str = "payload",
    path: str | Path | None = None,
) -> None:
    suffix = f": {path}" if path else ""
    embedded = embedded_date(data)
    if embedded is not None and not re.fullmatch(r"\d{8}", str(embedded)):
        raise ValueError(f"{label}: invalid embedded as_of_date={embedded}{suffix}")
    if embedded and embedded > requested_date:
        raise ValueError(f"{label}: embedded as_of_date {embedded} is later than requested {requested_date}{suffix}")
    if embedded and embedded < requested_date and not allow_stale:
        raise ValueError(f"{label}: embedded as_of_date {embedded} is stale for {requested_date}{suffix}")

    status = fallback_status(data)
    if status not in FALLBACK_STATUSES:
        raise ValueError(f"{label}: unsupported fallback_status={status}{suffix}")
    if status == "stale_fallback" and not allow_stale:
        raise ValueError(f"{label}: stale fallback is not allowed{suffix}")
    if status in {"static_fallback", "unverified"} and not allow_static:
        raise ValueError(f"{label}: {status} is not allowed for an official run{suffix}")
    if contains_static_marker(data) and not allow_static:
        raise ValueError(f"{label}: static/estimated source marker is not allowed{suffix}")

    future = future_date_fields(data, requested_date, label)
    if future:
        raise ValueError(f"{label}: future-dated observations are forbidden: {', '.join(future[:5])}{suffix}")


def require_fields(data: dict[str, Any], fields: Iterable[str], label: str) -> None:
    missing = [field for field in fields if data.get(field) is None]
    if missing:
        raise ValueError(f"{label}: missing required fields: {', '.join(missing)}")
