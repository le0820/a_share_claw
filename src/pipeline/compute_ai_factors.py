#!/usr/bin/env python3
"""Compute AI growth, momentum, sentiment and liquidity factor states."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from ai_strategy import growth_state, liquidity_state, momentum_state, sentiment_state
from pipeline_contracts import fallback_status, utc_now_iso, validate_payload
from pipeline_paths import AI_FACTORS_DIR, AI_MARKET_DIR, AI_RAW_DIR, ensure_runtime_dirs, validate_as_of_date


RULES_PATH = Path(__file__).resolve().parents[1] / "compiled" / "ai_strategy_rules.json"
SECTOR_PATH = AI_MARKET_DIR / "159819_SZ_daily.csv"
BENCHMARK_PATH = AI_MARKET_DIR / "510300_SH_daily.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute dated AI strategy factors.")
    parser.add_argument("--date", required=True, help="As-of date in YYYYMMDD format.")
    parser.add_argument(
        "--allow-unverified-inputs",
        "--allow-unverified-macro",
        dest="allow_unverified_inputs",
        action="store_true",
        help="Allow explicitly labelled unverified historical inputs for research replay.",
    )
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"missing AI pipeline input: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON input {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"AI pipeline input must be an object: {path}")
    return payload


def _load_close(path: Path, cutoff: str) -> pd.Series:
    if not path.is_file():
        raise SystemExit(f"missing AI market CSV: {path}")
    frame = pd.read_csv(path)
    if not {"trade_date", "close"}.issubset(frame.columns):
        raise SystemExit(f"AI market CSV missing trade_date/close: {path}")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    cutoff_ts = pd.to_datetime(cutoff, format="%Y%m%d")
    frame = frame.loc[frame["trade_date"] <= cutoff_ts].dropna(subset=["close"]).sort_values("trade_date")
    frame = frame.drop_duplicates("trade_date", keep="last")
    return pd.Series(frame["close"].to_numpy(), index=frame["trade_date"], dtype=float)


def main() -> None:
    args = parse_args()
    date = validate_as_of_date(args.date)
    ensure_runtime_dirs()
    rules = _load_json(RULES_PATH)
    paths = {
        "growth": AI_RAW_DIR / f"ai_growth_{date}.json",
        "market": AI_RAW_DIR / f"ai_market_{date}.json",
        "macro": AI_RAW_DIR / f"ai_macro_{date}.json",
        "sentiment": AI_RAW_DIR / f"cn_sentiment_{date}.json",
        "prices": AI_RAW_DIR / f"ai_prices_{date}.json",
    }
    growth_raw = _load_json(paths["growth"])
    market_raw = _load_json(paths["market"])
    macro_raw = _load_json(paths["macro"])
    sentiment_raw = _load_json(paths["sentiment"])

    validate_payload(
        growth_raw,
        date,
        allow_static=args.allow_unverified_inputs,
        label="AI growth",
        path=paths["growth"],
    )
    validate_payload(
        market_raw,
        date,
        allow_static=args.allow_unverified_inputs,
        label="AI market",
        path=paths["market"],
    )
    validate_payload(
        sentiment_raw,
        date,
        allow_static=args.allow_unverified_inputs,
        label="CN sentiment",
        path=paths["sentiment"],
    )
    macro_fallback = fallback_status(macro_raw)
    if macro_fallback == "unverified" and not args.allow_unverified_inputs:
        raise SystemExit(
            "AI macro is an unverified current-vintage historical replay; pass --allow-unverified-inputs "
            "only for labelled research backtests"
        )
    validate_payload(
        macro_raw,
        date,
        allow_static=args.allow_unverified_inputs,
        label="AI macro",
        path=paths["macro"],
    )

    effective_trade_date = market_raw["effective_trade_date"]
    sector = _load_close(SECTOR_PATH, effective_trade_date)
    benchmark = _load_close(BENCHMARK_PATH, effective_trade_date)
    factors = {
        "growth": growth_state(growth_raw, rules),
        "momentum": momentum_state(sector, benchmark, rules),
        "sentiment": sentiment_state(macro_raw, sentiment_raw, rules),
        "liquidity": liquidity_state(macro_raw, rules),
    }
    optional_price_status = "observed_not_scored_without_usage_or_availability"
    if not paths["prices"].is_file():
        optional_price_status = "missing_historical_snapshot"
    generated_at = utc_now_iso()
    official = all(
        fallback_status(payload) == "none"
        for payload in (growth_raw, market_raw, macro_raw, sentiment_raw)
    )
    output_fallback = "none" if official else "unverified"
    payload = {
        "schema_version": 1,
        "methodology_version": rules["version"],
        "generated_at": generated_at,
        "as_of_date": date,
        "effective_trade_date": effective_trade_date,
        "source": "deterministic AI factor engine",
        "source_timestamp": generated_at,
        "fallback_status": output_fallback,
        "official": official,
        "factors": factors,
        "optional_price_snapshot_status": optional_price_status,
        "data_audit": {
            "source_files": [str(path) for name, path in paths.items() if name != "prices" or path.is_file()],
            "market_files": [str(SECTOR_PATH), str(BENCHMARK_PATH)],
            "macro_vintage_status": macro_raw.get("vintage_status"),
            "fallback_status": output_fallback,
            "future_data_filtered": True,
        },
    }
    output_path = AI_FACTORS_DIR / f"ai_factors_{date}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[ai-factors] {date}/{effective_trade_date}: growth={factors['growth']['regime']} "
        f"M={factors['momentum']['level_percentile']} E={factors['sentiment']['state_percentile']} "
        f"L={factors['liquidity']['tightening_percentile']} -> {output_path}"
    )


if __name__ == "__main__":
    main()
