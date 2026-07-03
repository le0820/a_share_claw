from __future__ import annotations

import csv
import io
import json
import urllib.parse
from typing import Any

from .http_client import fetch_json, fetch_text
from .web_search import search_web


EASTMONEY_FIELDS = ",".join(
    [
        "f57",
        "f58",
        "f43",
        "f44",
        "f45",
        "f46",
        "f47",
        "f48",
        "f60",
        "f169",
        "f170",
        "f116",
        "f117",
        "f162",
        "f167",
        "f168",
    ]
)


async def get_a_share_quote(symbol: str) -> str:
    secid = eastmoney_secid(symbol)
    url = (
        "https://push2.eastmoney.com/api/qt/stock/get?"
        + urllib.parse.urlencode({"secid": secid, "fields": EASTMONEY_FIELDS})
    )
    data = await fetch_json(url, timeout=15)
    raw = data.get("data")
    if not raw:
        return json.dumps({"error": f"no quote data for {symbol}", "source": url}, ensure_ascii=False)
    quote = {
        "source": url,
        "symbol": raw.get("f57"),
        "name": raw.get("f58"),
        "last": _price(raw.get("f43")),
        "open": _price(raw.get("f46")),
        "high": _price(raw.get("f44")),
        "low": _price(raw.get("f45")),
        "previous_close": _price(raw.get("f60")),
        "change": _price(raw.get("f169")),
        "pct_change": _percent(raw.get("f170")),
        "volume_lots": raw.get("f47"),
        "amount_cny": raw.get("f48"),
        "market_cap_cny": raw.get("f116"),
        "free_float_market_cap_cny": raw.get("f117"),
        "pe_ttm": _ratio(raw.get("f162")),
        "pb": _ratio(raw.get("f167")),
        "turnover_pct": _percent(raw.get("f168")),
    }
    return json.dumps(quote, ensure_ascii=False)


async def get_fred_series(series_id: str, limit: int = 24) -> str:
    clean_id = series_id.strip().upper()
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode({"id": clean_id})
    text = await fetch_text(url, timeout=20, max_bytes=1_000_000)
    reader = csv.DictReader(io.StringIO(text))
    rows = [row for row in reader if row.get(clean_id) not in (None, ".")]
    tail = rows[-max(1, min(limit, 200)) :]
    return json.dumps({"source": url, "series_id": clean_id, "observations": tail}, ensure_ascii=False)


async def industry_research_links(industry: str, max_results: int = 6) -> str:
    query = f"{industry} A股 行业 基本面 景气度 价格 产能 政策 研报"
    return await search_web(query, max_results=max_results)


def eastmoney_secid(symbol: str) -> str:
    value = symbol.strip().upper()
    if "." in value:
        code, suffix = value.split(".", 1)
        if suffix in {"SH", "SS"}:
            return f"1.{code}"
        if suffix in {"SZ", "BJ"}:
            return f"0.{code}"
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid A-share symbol: {symbol}")
    market = "1" if digits.startswith(("5", "6", "9")) else "0"
    return f"{market}.{digits}"


def _price(value: Any) -> float | None:
    if value in (None, "-", ""):
        return None
    return round(float(value) / 100, 4)


def _percent(value: Any) -> float | None:
    if value in (None, "-", ""):
        return None
    return round(float(value) / 100, 4)


def _ratio(value: Any) -> float | None:
    if value in (None, "-", ""):
        return None
    return round(float(value) / 100, 4)
