#!/usr/bin/env python3
"""Fetch the configured scoring-asset daily bars with easy-tdx."""

from __future__ import annotations

import json
import os
import sys
from contextlib import ExitStack
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pandas as pd
from easy_tdx import Adjust, ExMarket, MacClient, MacExClient, Market, Period

from pipeline_cli import parse_fetch_etf_days
from pipeline_contracts import utc_now_iso
from pipeline_paths import MARKET_DIR, RAW_DIR, ensure_runtime_dirs
from pipeline_universe import market_csv_path, scoring_assets


OUTPUT_MANIFEST = RAW_DIR / "market_manifest.json"
REQUIRED_COLUMNS = {"trade_date", "open", "high", "low", "close"}
MARKETS = {"SH": Market.SH, "SZ": Market.SZ, "BJ": Market.BJ}
EX_MARKETS = {"US_STOCK": ExMarket.US_STOCK}
ADJUSTMENTS = {"NONE": Adjust.NONE, "QFQ": Adjust.QFQ, "HFQ": Adjust.HFQ}


def _rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "datetime" in df.columns:
        df = df.rename(columns={"datetime": "trade_date"})
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"easy-tdx response missing columns: {', '.join(missing)}")
    if df.empty:
        raise ValueError("easy-tdx returned an empty DataFrame")
    result = df.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise")
    return result.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def _fetch(client: Any, spec: dict[str, Any], count: int) -> pd.DataFrame:
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
        raise ValueError(f"unknown easy-tdx client type: {spec['client']}")
    return _rename_columns(frame)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def fetch_and_save(symbols: dict[str, dict[str, Any]], count: int, label: str) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    with ExitStack() as stack:
        clients: dict[str, Any] = {}
        for symbol, spec in symbols.items():
            path = market_csv_path(symbol)
            try:
                client_type = spec["client"]
                if client_type not in clients:
                    factory = MacClient if client_type == "mac" else MacExClient
                    clients[client_type] = stack.enter_context(factory.from_best_host())
                frame = _fetch(clients[client_type], spec, count)
                _write_csv(frame, path)
                first_date = frame["trade_date"].min().strftime("%Y-%m-%d")
                last_date = frame["trade_date"].max().strftime("%Y-%m-%d")
                print(f"  [{label}] {symbol} ({spec['name']}): {len(frame)} rows, {first_date}..{last_date} -> {path}")
                manifest[symbol] = {
                    "name": spec["name"],
                    "path": str(path),
                    "provider": "easy-tdx",
                    "provider_market": spec["provider_market"],
                    "provider_code": spec["provider_code"],
                    "adjustment": spec["adjustment"],
                    "rows": len(frame),
                    "first_trade_date": first_date,
                    "last_trade_date": last_date,
                    "fallback_status": "none",
                }
            except Exception as exc:
                print(f"  [{label}] {symbol} ({spec['name']}): FAILED - {exc}", file=sys.stderr)
                manifest[symbol] = {
                    "name": spec["name"],
                    "path": None,
                    "provider": "easy-tdx",
                    "provider_market": spec["provider_market"],
                    "provider_code": spec["provider_code"],
                    "adjustment": spec["adjustment"],
                    "error": str(exc),
                    "fallback_status": "unverified",
                }
    return manifest


def main() -> None:
    days = parse_fetch_etf_days()
    ensure_runtime_dirs()
    assets = scoring_assets()
    print(f"[fetch_etf] fetching {len(assets)} configured symbols (count={days})")
    print("[fetch_etf] source: easy-tdx MacClient + MacExClient")

    generated_at = utc_now_iso()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source": "easy-tdx",
        "source_version": version("easy-tdx"),
        "source_timestamp": generated_at,
        "days": days,
        "market_data_dir": str(MARKET_DIR),
        "fallback_status": "none",
        "assets": fetch_and_save(assets, days, "ASSET"),
    }
    entries = list(manifest["assets"].values())
    failures = [entry for entry in entries if entry.get("path") is None]
    if failures:
        manifest["fallback_status"] = "unverified"

    OUTPUT_MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[fetch_etf] completed: {len(entries) - len(failures)}/{len(entries)} -> {OUTPUT_MANIFEST}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
