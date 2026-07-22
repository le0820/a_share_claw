#!/usr/bin/env python3
"""Fetch the independent A-share AI sector proxy and benchmark with easy-tdx."""

from __future__ import annotations

import argparse
import io
import json
import os
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from easy_tdx import Adjust, ExMarket, MacClient, MacExClient, Market, Period

from pipeline_contracts import utc_now_iso
from pipeline_paths import AI_MARKET_DIR, AI_RAW_DIR, current_date, ensure_runtime_dirs, validate_as_of_date


UNIVERSE_PATH = Path(__file__).with_name("ai_tactical_universe.json")
CSI_CONSTITUENTS_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "file/autofile/cons/930713cons.xls"
)
MARKETS = {"SH": Market.SH, "SZ": Market.SZ, "BJ": Market.BJ}
EX_MARKETS = {"US_STOCK": ExMarket.US_STOCK}
ADJUSTMENTS = {"NONE": Adjust.NONE, "QFQ": Adjust.QFQ, "HFQ": Adjust.HFQ}
REQUIRED_COLUMNS = {"trade_date", "open", "high", "low", "close"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch AI sector and benchmark daily bars.")
    parser.add_argument("--date", required=True, help="Requested A-share date in YYYYMMDD format.")
    parser.add_argument("--days", type=int, default=800, help="Daily bars to fetch (default: 800).")
    parser.add_argument(
        "--fetch-constituents",
        action="store_true",
        help="Archive the current CSI 930713 constituent snapshot. Historical dates never fetch a current snapshot.",
    )
    parser.add_argument(
        "--reuse-market-files",
        action="store_true",
        help="Build a dated manifest from already archived easy-tdx CSV files without a network refresh.",
    )
    return parser.parse_args()


def load_universe() -> dict[str, Any]:
    payload = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise RuntimeError("unsupported AI tactical universe schema")
    return payload


def safe_symbol(symbol: str) -> str:
    return symbol.replace(".", "_")


def market_path(symbol: str) -> Path:
    return AI_MARKET_DIR / f"{safe_symbol(symbol)}_daily.csv"


def _fetch_frame(client: Any, spec: dict[str, Any], count: int) -> pd.DataFrame:
    adjustment = ADJUSTMENTS[spec["adjustment"]]
    if spec["client"] == "mac":
        frame = client.get_stock_kline(
            MARKETS[spec["provider_market"]],
            spec["provider_code"],
            Period.DAILY,
            count=count,
            adjust=adjustment,
        )
    elif spec["client"] == "mac_ex":
        frame = client.goods_kline(
            EX_MARKETS[spec["provider_market"]],
            spec["provider_code"],
            Period.DAILY,
            count=count,
            adjust=adjustment,
        )
    else:
        raise ValueError(f"unsupported easy-tdx client: {spec['client']}")
    if "datetime" in frame.columns:
        frame = frame.rename(columns={"datetime": "trade_date"})
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"easy-tdx response missing columns: {', '.join(missing)}")
    result = frame.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise")
    return result.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def fetch_assets(universe: dict[str, Any], count: int, as_of_date: str) -> tuple[dict[str, Any], str]:
    assets = [universe["sector_proxy"], universe["benchmark"]]
    manifest: dict[str, Any] = {}
    effective_dates: list[str] = []
    with ExitStack() as stack:
        clients: dict[str, Any] = {}
        for spec in assets:
            client_type = spec["client"]
            if client_type not in clients:
                factory = MacClient if client_type == "mac" else MacExClient
                clients[client_type] = stack.enter_context(factory.from_best_host())
            frame = _fetch_frame(clients[client_type], spec, count)
            path = market_path(spec["symbol"])
            _write_csv(frame, path)
            cutoff = pd.to_datetime(as_of_date, format="%Y%m%d")
            eligible = frame.loc[frame["trade_date"] <= cutoff]
            if eligible.empty:
                raise RuntimeError(f"{spec['symbol']} has no close on or before {as_of_date}")
            effective = eligible["trade_date"].iloc[-1].strftime("%Y%m%d")
            effective_dates.append(effective)
            manifest[spec["symbol"]] = {
                "name": spec["name"],
                "path": str(path),
                "provider": "easy-tdx",
                "provider_market": spec["provider_market"],
                "provider_code": spec["provider_code"],
                "adjustment": spec["adjustment"],
                "rows": len(frame),
                "first_trade_date": frame["trade_date"].min().strftime("%Y-%m-%d"),
                "last_trade_date": frame["trade_date"].max().strftime("%Y-%m-%d"),
                "last_trade_date_used": pd.to_datetime(effective, format="%Y%m%d").strftime("%Y-%m-%d"),
                "fallback_status": "none",
            }
    return manifest, min(effective_dates)


def reuse_assets(universe: dict[str, Any], as_of_date: str) -> tuple[dict[str, Any], str]:
    manifest: dict[str, Any] = {}
    effective_dates: list[str] = []
    cutoff = pd.to_datetime(as_of_date, format="%Y%m%d")
    for spec in (universe["sector_proxy"], universe["benchmark"]):
        path = market_path(spec["symbol"])
        if not path.is_file():
            raise RuntimeError(f"archived market file missing: {path}")
        frame = pd.read_csv(path)
        missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
        if missing:
            raise RuntimeError(f"archived market file {path} missing columns: {', '.join(missing)}")
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
        frame = frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
        eligible = frame.loc[frame["trade_date"] <= cutoff]
        if eligible.empty:
            raise RuntimeError(f"{spec['symbol']} has no archived close on or before {as_of_date}")
        effective = eligible["trade_date"].iloc[-1].strftime("%Y%m%d")
        effective_dates.append(effective)
        manifest[spec["symbol"]] = {
            "name": spec["name"],
            "path": str(path),
            "provider": "easy-tdx archived workspace CSV",
            "provider_market": spec["provider_market"],
            "provider_code": spec["provider_code"],
            "adjustment": spec["adjustment"],
            "rows": len(frame),
            "first_trade_date": frame["trade_date"].min().strftime("%Y-%m-%d"),
            "last_trade_date": frame["trade_date"].max().strftime("%Y-%m-%d"),
            "last_trade_date_used": pd.to_datetime(effective, format="%Y%m%d").strftime("%Y-%m-%d"),
            "fallback_status": "none",
        }
    return manifest, min(effective_dates)


def _http_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = os.environ.get("ASCLAW_DATA_TRUST_ENV", "0").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    session.headers.update({"User-Agent": "a-share-claw/0.1 personal investment research"})
    return session


def fetch_current_constituents(date: str) -> dict[str, Any]:
    if date != current_date().strftime("%Y%m%d"):
        return {
            "status": "omitted_historical_snapshot_missing",
            "reason": "current CSI constituents cannot backfill a historical as_of_date",
        }
    response = _http_session().get(CSI_CONSTITUENTS_URL, timeout=60)
    response.raise_for_status()
    frame = pd.read_excel(io.BytesIO(response.content))
    if frame.empty or len(frame.columns) < 6:
        raise RuntimeError("CSI constituent workbook is empty or has an unsupported schema")
    frame.columns = [
        "effective_date",
        "index_code",
        "index_name",
        "index_name_en",
        "security_code",
        "security_name",
        "security_name_en",
        "exchange",
        "exchange_en",
    ][: len(frame.columns)]
    frame["effective_date"] = pd.to_datetime(
        frame["effective_date"].astype(str), format="%Y%m%d", errors="coerce"
    )
    frame["security_code"] = frame["security_code"].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
    effective = frame["effective_date"].max().strftime("%Y-%m-%d")
    output_path = AI_RAW_DIR / f"csi_930713_constituents_{date}.csv"
    frame.to_csv(output_path, index=False)
    return {
        "status": "archived",
        "path": str(output_path),
        "source": "CSI 930713 official constituent workbook",
        "source_url": CSI_CONSTITUENTS_URL,
        "effective_date": effective,
        "rows": len(frame),
        "fallback_status": "none",
    }


def main() -> None:
    args = parse_args()
    date = validate_as_of_date(args.date)
    if args.days < 300:
        raise SystemExit("--days must be at least 300 for 12-1 momentum")
    ensure_runtime_dirs()
    universe = load_universe()
    assets, effective_trade_date = (
        reuse_assets(universe, date)
        if args.reuse_market_files
        else fetch_assets(universe, args.days, date)
    )
    constituents = (
        fetch_current_constituents(date)
        if args.fetch_constituents
        else {"status": "not_requested", "fallback_status": "none"}
    )
    generated_at = utc_now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "as_of_date": date,
        "effective_trade_date": effective_trade_date,
        "source": "easy-tdx; CSI constituent snapshot when explicitly requested",
        "source_timestamp": generated_at,
        "fallback_status": "none",
        "universe_file": str(UNIVERSE_PATH),
        "assets": assets,
        "constituents": constituents,
        "data_audit": {
            "filtered_to_as_of_date": True,
            "market_fetch_mode": "reuse_archived_csv" if args.reuse_market_files else "network_refresh",
            "source_files": [str(market_path(spec["symbol"])) for spec in (universe["sector_proxy"], universe["benchmark"])],
            "fallback_status": "none",
        },
    }
    output_path = AI_RAW_DIR / f"ai_market_{date}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ai-market] requested={date} effective_trade_date={effective_trade_date} -> {output_path}")


if __name__ == "__main__":
    main()
