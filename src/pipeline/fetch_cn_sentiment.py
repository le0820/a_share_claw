#!/usr/bin/env python3
"""Fetch point-in-time A-share margin and SSE option sentiment histories."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import pandas as pd
import requests

from pipeline_contracts import utc_now_iso
from pipeline_paths import AI_MARKET_DIR, AI_RAW_DIR, ensure_runtime_dirs, validate_as_of_date


SSE_MARGIN_URL = "https://query.sse.com.cn/marketdata/tradedata/queryMargin.do"
SZSE_MARGIN_URL = "https://www.szse.cn/api/report/ShowReport/data"
SSE_OPTION_URL = "https://query.sse.com.cn/commonQuery.do"
SECTOR_MARKET_PATH = AI_MARKET_DIR / "159819_SZ_daily.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch official A-share sentiment histories.")
    parser.add_argument("--date", required=True, help="Requested date in YYYYMMDD format.")
    parser.add_argument("--lookback", type=int, default=90, help="Trading sessions to retain (default: 90).")
    parser.add_argument(
        "--margin-lag-sessions",
        type=int,
        default=1,
        choices=(0, 1),
        help="Use 1 for an A-share close signal; same-day margin is published before the next open.",
    )
    parser.add_argument(
        "--sse-only",
        action="store_true",
        help="Research fallback: use a consistent SSE-only margin proxy and label the artifact unverified.",
    )
    return parser.parse_args()


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = os.environ.get("ASCLAW_DATA_TRUST_ENV", "0").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    session.headers.update({"User-Agent": "Mozilla/5.0 a-share-claw personal research"})
    return session


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def _trade_dates(requested_date: str, lookback: int) -> tuple[list[str], str]:
    if not SECTOR_MARKET_PATH.is_file():
        raise SystemExit(f"[cn-sentiment] missing AI sector market file: {SECTOR_MARKET_PATH}")
    frame = pd.read_csv(SECTOR_MARKET_PATH, usecols=["trade_date"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    cutoff = pd.to_datetime(requested_date, format="%Y%m%d")
    eligible = frame.loc[frame["trade_date"] <= cutoff, "trade_date"].drop_duplicates().sort_values()
    if len(eligible) < lookback + 2:
        raise SystemExit(f"[cn-sentiment] only {len(eligible)} trade dates; need {lookback + 2}")
    dates = [value.strftime("%Y%m%d") for value in eligible.iloc[-(lookback + 2) :]]
    return dates, dates[-1]


def _fetch_sse_margin(session: requests.Session, begin: str, end: str) -> dict[str, dict[str, float]]:
    response = session.get(
        SSE_MARGIN_URL,
        params={
            "isPagination": "true",
            "beginDate": begin,
            "endDate": end,
            "tabType": "",
            "stockCode": "",
            "pageHelp.pageSize": "5000",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "5",
        },
        headers={"Referer": "https://www.sse.com.cn/"},
        timeout=60,
    )
    response.raise_for_status()
    rows = response.json().get("result", [])
    output: dict[str, dict[str, float]] = {}
    for row in rows:
        date = str(row.get("opDate") or "")
        balance = _number(row.get("rzye"))
        buy = _number(row.get("rzmre"))
        if len(date) == 8 and balance is not None and buy is not None:
            output[date] = {"financing_balance_yuan": balance, "financing_buy_yuan": buy}
    return output


def _fetch_szse_margin(date: str) -> dict[str, float] | None:
    session = _session()
    response = session.get(
        SZSE_MARGIN_URL,
        params={
            "SHOWTYPE": "JSON",
            "CATALOGID": "1837_xxpl",
            "txtDate": f"{date[:4]}-{date[4:6]}-{date[6:]}",
            "tab1PAGENO": "1",
            "random": "0.7425245522795993",
        },
        headers={"Referer": "https://www.szse.cn/disclosure/margin/object/index.html"},
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload or not payload[0].get("data"):
        return None
    row = payload[0]["data"][0]
    balance_100m = _number(row.get("jrrzye"))
    buy_100m = _number(row.get("jrrzmr"))
    if balance_100m is None or buy_100m is None:
        return None
    return {
        "financing_balance_yuan": balance_100m * 100_000_000,
        "financing_buy_yuan": buy_100m * 100_000_000,
    }


def _fetch_sse_options(date: str) -> dict[str, float | None] | None:
    session = _session()
    response = session.get(
        SSE_OPTION_URL,
        params={
            "isPagination": "true",
            "sqlId": "COMMON_SSE_ZQPZ_YSP_QQ_SJTJ_MRTJ_CX",
            "tradeDate": date,
            "pageHelp.pageSize": "25",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "5",
        },
        headers={"Referer": "https://www.sse.com.cn/assortment/options/date/"},
        timeout=45,
    )
    response.raise_for_status()
    rows = response.json().get("result", [])
    if not rows:
        return None

    def aggregate(selected: list[dict[str, Any]]) -> tuple[float | None, float | None]:
        calls = sum(_number(row.get("CALL_VOLUME")) or 0.0 for row in selected)
        puts = sum(_number(row.get("PUT_VOLUME")) or 0.0 for row in selected)
        call_oi = sum(_number(row.get("LEAVES_CALL_QTY")) or 0.0 for row in selected)
        put_oi = sum(_number(row.get("LEAVES_PUT_QTY")) or 0.0 for row in selected)
        return (puts / calls if calls else None, put_oi / call_oi if call_oi else None)

    broad_volume, broad_oi = aggregate(rows)
    growth_rows = [row for row in rows if str(row.get("SECURITY_CODE", "")).startswith("588")]
    growth_volume, growth_oi = aggregate(growth_rows)
    return {
        "broad_put_call_volume": round(broad_volume, 6) if broad_volume is not None else None,
        "broad_put_call_open_interest": round(broad_oi, 6) if broad_oi is not None else None,
        "growth_put_call_volume": round(growth_volume, 6) if growth_volume is not None else None,
        "growth_put_call_open_interest": round(growth_oi, 6) if growth_oi is not None else None,
    }


def main() -> None:
    args = parse_args()
    requested = validate_as_of_date(args.date)
    if args.lookback < 45:
        raise SystemExit("--lookback must be at least 45 sessions")
    ensure_runtime_dirs()
    trade_dates, effective_trade_date = _trade_dates(requested, args.lookback)
    session = _session()
    sse_margin = _fetch_sse_margin(session, trade_dates[0], effective_trade_date)

    szse_margin: dict[str, dict[str, float]] = {}
    option_stats: dict[str, dict[str, float | None]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for date in trade_dates:
            if not args.sse_only:
                futures[executor.submit(_fetch_szse_margin, date)] = ("szse", date)
            futures[executor.submit(_fetch_sse_options, date)] = ("options", date)
        for future in as_completed(futures):
            kind, date = futures[future]
            try:
                value = future.result()
                if value is None:
                    errors.append(f"{kind}:{date}: no data")
                elif kind == "szse":
                    szse_margin[date] = value
                else:
                    option_stats[date] = value
            except Exception as exc:
                errors.append(f"{kind}:{date}: {exc}")

    next_trade = {trade_dates[index]: trade_dates[index + 1] for index in range(len(trade_dates) - 1)}
    combined_margin_rows: list[dict[str, Any]] = []
    requested_cutoff = datetime.strptime(requested, "%Y%m%d").date()
    for date in trade_dates:
        if date not in sse_margin or date not in szse_margin:
            continue
        release = next_trade.get(date)
        release_date = datetime.strptime(release, "%Y%m%d").date() if release else None
        if release_date is None or release_date > requested_cutoff:
            continue
        combined_margin_rows.append(
            {
                "trade_date": datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d"),
                "release_date": release_date.isoformat(),
                "financing_balance_yuan": round(
                    sse_margin[date]["financing_balance_yuan"] + szse_margin[date]["financing_balance_yuan"], 2
                ),
                "financing_buy_yuan": round(
                    sse_margin[date]["financing_buy_yuan"] + szse_margin[date]["financing_buy_yuan"], 2
                ),
            }
        )
    options_rows = [
        {
            "trade_date": datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d"),
            "publication_date": datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d"),
            **option_stats[date],
        }
        for date in trade_dates
        if date in option_stats
    ]
    margin_cutoff_index = -(args.margin_lag_sessions + 1)
    margin_cutoff_date = trade_dates[margin_cutoff_index]
    expected_margin_cutoff = datetime.strptime(margin_cutoff_date, "%Y%m%d").strftime("%Y-%m-%d")
    latest_combined_date = combined_margin_rows[-1]["trade_date"] if combined_margin_rows else None
    if not args.sse_only and len(combined_margin_rows) >= 40 and latest_combined_date == expected_margin_cutoff:
        margin_rows = combined_margin_rows
        margin_scope = "SSE_and_SZSE"
        output_fallback = "none"
    else:
        # Never splice a later SSE-only segment onto an earlier combined
        # series. Use one consistent official-exchange proxy over the full
        # history and label the reduced market coverage explicitly.
        margin_rows = []
        for date in trade_dates:
            if date not in sse_margin:
                continue
            release = next_trade.get(date)
            release_date = datetime.strptime(release, "%Y%m%d").date() if release else None
            if release_date is None or release_date > requested_cutoff:
                continue
            margin_rows.append(
                {
                    "trade_date": datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d"),
                    "release_date": release_date.isoformat(),
                    "financing_balance_yuan": round(sse_margin[date]["financing_balance_yuan"], 2),
                    "financing_buy_yuan": round(sse_margin[date]["financing_buy_yuan"], 2),
                }
            )
        margin_scope = "SSE_only_proxy"
        output_fallback = "unverified"
    if len(margin_rows) < 40:
        raise SystemExit(f"[cn-sentiment] only {len(margin_rows)} consistent official margin rows; need 40")
    if len(options_rows) < 20:
        raise SystemExit(f"[cn-sentiment] only {len(options_rows)} official option rows; need 20")

    generated_at = utc_now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "as_of_date": requested,
        "effective_trade_date": effective_trade_date,
        "signal_cutoff": "A-share close",
        "margin_lag_sessions": args.margin_lag_sessions,
        "margin_data_cutoff": datetime.strptime(margin_cutoff_date, "%Y%m%d").strftime("%Y-%m-%d"),
        "source": (
            "SSE and SZSE official margin reports; SSE official daily option statistics"
            if margin_scope == "SSE_and_SZSE"
            else "SSE official margin-market proxy; SSE official daily option statistics"
        ),
        "source_timestamp": generated_at,
        "data_period": f"{trade_dates[0]}..{effective_trade_date}",
        "fallback_status": output_fallback,
        "margin_market_scope": margin_scope,
        "margin_history": margin_rows,
        "option_history": options_rows,
        "leverage_ratio": None,
        "omitted_fields": [
            "margin_balance/free_float_market_cap: no point-in-time official market-cap denominator implemented",
            *(
                ["SZSE margin history unavailable or explicitly skipped; SSE-only proxy used consistently"]
                if margin_scope == "SSE_only_proxy"
                else []
            ),
        ],
        "source_urls": {
            "sse_margin": SSE_MARGIN_URL,
            "szse_margin": SZSE_MARGIN_URL,
            "sse_options": SSE_OPTION_URL,
        },
        "data_audit": {
            "official_exchange_rows": True,
            "margin_rows": len(margin_rows),
            "combined_margin_rows": len(combined_margin_rows),
            "latest_combined_margin_date": latest_combined_date,
            "expected_margin_cutoff": expected_margin_cutoff,
            "margin_market_scope": margin_scope,
            "option_rows": len(options_rows),
            "request_errors": errors,
            "sse_only_requested": args.sse_only,
            "fallback_status": output_fallback,
        },
    }
    output_path = AI_RAW_DIR / f"cn_sentiment_{requested}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[cn-sentiment] requested={requested} effective={effective_trade_date} "
        f"margin_cutoff={margin_cutoff_date} -> {output_path}"
    )


if __name__ == "__main__":
    main()
