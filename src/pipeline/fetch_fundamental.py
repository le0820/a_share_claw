#!/usr/bin/env python3
"""Validate and aggregate host/MCP-produced ETF fundamental evidence.

This script does not call Tavily/QVeris itself: MCP tools live in the agent host.
The host materializes ``fundamental_inputs_<DATE>.json`` and this deterministic
adapter validates dates, provenance, holdings coverage, and weighted metrics.
The derived ``fundamental_<DATE>.json`` is optional research evidence and does
not participate in the currently disabled L2 layer.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pipeline_contracts import require_fields, utc_now_iso, validate_payload
from pipeline_paths import RAW_DIR, ensure_runtime_dirs, validate_as_of_date


MIN_COVERAGE = 0.80
METRICS = ("roe_pct", "operating_margin_pct", "eps_growth_pct", "market_cap")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and aggregate dated ETF fundamental evidence.")
    parser.add_argument("legacy_date", nargs="?", help="Backward-compatible YYYYMMDD date.")
    parser.add_argument("--date", dest="date", help="As-of date, YYYYMMDD.")
    parser.add_argument("--input", type=Path, help="Canonical evidence input; defaults to data/raw/fundamental_inputs_DATE.json.")
    args = parser.parse_args()
    if not args.date and not args.legacy_date:
        parser.error("--date is required")
    date = args.date or args.legacy_date
    try:
        args.date = validate_as_of_date(date)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def _require_source(row: dict[str, Any], label: str) -> None:
    require_fields(row, ("source", "source_url", "publication_date"), label)


def _weighted_metric(
    holdings: list[dict[str, Any]],
    fundamentals: dict[str, dict[str, Any]],
    metric: str,
) -> tuple[float | None, float]:
    weighted = 0.0
    coverage = 0.0
    for holding in holdings:
        record = fundamentals.get(str(holding["symbol"]))
        if record is None or record.get(metric) is None:
            continue
        weight = float(holding["weight"])
        weighted += float(record[metric]) * weight
        coverage += weight
    return ((weighted / coverage) if coverage else None), coverage


def aggregate(payload: dict[str, Any], as_of_date: str, source_path: Path) -> dict[str, Any]:
    validate_payload(payload, as_of_date, label="fundamental inputs", path=source_path)
    if payload.get("schema_version") != 1:
        raise ValueError("fundamental inputs: schema_version must be 1")
    funds = payload.get("funds")
    if not isinstance(funds, dict) or not funds:
        raise ValueError("fundamental inputs: funds must be a non-empty object")

    output_funds: dict[str, Any] = {}
    source_refs: list[dict[str, str]] = []
    for fund_symbol, fund in funds.items():
        if not isinstance(fund, dict):
            raise ValueError(f"{fund_symbol}: fund payload must be an object")
        holdings = fund.get("holdings")
        raw_fundamentals = fund.get("fundamentals")
        if not isinstance(holdings, list) or not holdings:
            raise ValueError(f"{fund_symbol}: holdings must be a non-empty list")
        if not isinstance(raw_fundamentals, list) or not raw_fundamentals:
            raise ValueError(f"{fund_symbol}: fundamentals must be a non-empty list")

        normalized_holdings: list[dict[str, Any]] = []
        seen_holdings: set[str] = set()
        for index, holding in enumerate(holdings):
            label = f"{fund_symbol}.holdings[{index}]"
            if not isinstance(holding, dict):
                raise ValueError(f"{label}: row must be an object")
            require_fields(holding, ("symbol", "weight", "effective_date"), label)
            _require_source(holding, label)
            symbol = str(holding["symbol"])
            if symbol in seen_holdings:
                raise ValueError(f"{fund_symbol}: duplicate holding {symbol}")
            weight = float(holding["weight"])
            if not 0 < weight <= 1:
                raise ValueError(f"{label}: weight must use 0..1 units")
            seen_holdings.add(symbol)
            normalized_holdings.append({**holding, "symbol": symbol, "weight": weight})
            source_refs.append({"source": str(holding["source"]), "source_url": str(holding["source_url"])})

        total_weight = sum(float(row["weight"]) for row in normalized_holdings)
        if total_weight > 1.01:
            raise ValueError(f"{fund_symbol}: holdings weights exceed 1.0 ({total_weight:.4f})")
        if total_weight < MIN_COVERAGE:
            raise ValueError(f"{fund_symbol}: holdings coverage {total_weight:.2%} is below {MIN_COVERAGE:.0%}")

        fundamentals: dict[str, dict[str, Any]] = {}
        for index, record in enumerate(raw_fundamentals):
            label = f"{fund_symbol}.fundamentals[{index}]"
            if not isinstance(record, dict):
                raise ValueError(f"{label}: row must be an object")
            require_fields(record, ("symbol", "period_end", "filing_date"), label)
            _require_source(record, label)
            symbol = str(record["symbol"])
            if symbol in fundamentals:
                raise ValueError(f"{fund_symbol}: duplicate fundamental record {symbol}")
            if not any(record.get(metric) is not None for metric in METRICS):
                raise ValueError(f"{label}: at least one supported metric is required")
            fundamentals[symbol] = record
            source_refs.append({"source": str(record["source"]), "source_url": str(record["source_url"])})

        metric_values: dict[str, Any] = {}
        metric_coverage: dict[str, float] = {}
        for metric in METRICS:
            value, coverage = _weighted_metric(normalized_holdings, fundamentals, metric)
            metric_values[metric] = round(value, 4) if value is not None else None
            metric_coverage[metric] = round(coverage, 6)

        output_funds[fund_symbol] = {
            "holdings_effective_date": max(str(row["effective_date"]) for row in normalized_holdings),
            "coverage_weight": round(total_weight, 6),
            "missing_weight": round(max(0.0, 1.0 - total_weight), 6),
            "aggregation_method": "weighted_mean_over_observed_constituents_without_residual_estimation",
            "metrics": metric_values,
            "metric_coverage": metric_coverage,
            "constituents_observed": len(normalized_holdings),
            "constituents_with_fundamentals": len(fundamentals),
        }

    generated_at = utc_now_iso()
    unique_refs = list({(row["source"], row["source_url"]): row for row in source_refs}.values())
    return {
        "schema_version": 1,
        "as_of_date": as_of_date,
        "generated_at": generated_at,
        "source": str(source_path),
        "source_refs": unique_refs,
        "fallback_status": "none",
        "_meta": {
            "as_of_date": as_of_date,
            "generated_at": generated_at,
            "source": str(source_path),
            "source_timestamp": generated_at,
            "data_period": "holdings effective date and filing periods published by as_of_date",
            "fallback_status": "none",
        },
        "funds": output_funds,
    }


def main() -> None:
    args = _parse_args()
    ensure_runtime_dirs()
    source_path = args.input or (RAW_DIR / f"fundamental_inputs_{args.date}.json")
    if not source_path.is_file():
        raise SystemExit(
            f"[fundamental] missing canonical evidence input: {source_path}. "
            "Materialize it from the host Tavily/QVeris evidence workflow; static constituents are forbidden."
        )
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        output = aggregate(payload, args.date, source_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"[fundamental] invalid evidence: {exc}") from exc

    output_path = RAW_DIR / f"fundamental_{args.date}.json"
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[fundamental] validated evidence -> {output_path}")


if __name__ == "__main__":
    main()
