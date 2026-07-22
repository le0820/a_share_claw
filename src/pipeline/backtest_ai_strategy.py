#!/usr/bin/env python3
"""Combine exact dated AI signals into a small point-in-time replay report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pipeline_contracts import utc_now_iso
from pipeline_paths import AI_BACKTEST_DIR, AI_SIGNALS_DIR, ensure_runtime_dirs, validate_as_of_date


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an AI strategy event replay from existing dated signals.")
    parser.add_argument("--dates", nargs="+", required=True, help="Effective trade dates in YYYYMMDD format.")
    parser.add_argument(
        "--requested-date-map",
        action="append",
        default=[],
        metavar="REQUESTED:EFFECTIVE",
        help="Record a non-trading requested date mapping, for example 20260620:20260618.",
    )
    return parser.parse_args()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"missing dated AI signal: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"invalid dated AI signal: {path}")
    return payload


def main() -> None:
    args = parse_args()
    dates = [validate_as_of_date(value) for value in args.dates]
    ensure_runtime_dirs()
    requested_map: dict[str, str] = {}
    for item in args.requested_date_map:
        try:
            requested, effective = item.split(":", 1)
        except ValueError as exc:
            raise SystemExit("--requested-date-map must use REQUESTED:EFFECTIVE") from exc
        requested_map[validate_as_of_date(requested)] = validate_as_of_date(effective)

    rows: list[dict[str, Any]] = []
    source_files: list[str] = []
    for date in dates:
        path = AI_SIGNALS_DIR / f"ai_signal_{date}.json"
        signal = _load(path)
        source_files.append(str(path))
        factors = signal["factor_summary"]
        decision = signal["decision"]
        requested_dates = [requested for requested, effective in requested_map.items() if effective == date]
        rows.append(
            {
                "requested_dates": requested_dates or [date],
                "as_of_date": date,
                "effective_trade_date": signal["effective_trade_date"],
                "official": signal["official"],
                "fallback_status": signal["fallback_status"],
                "growth_regime": factors["growth"]["regime"],
                "growth_score": factors["growth"]["score"],
                "momentum_level": factors["momentum"]["level_percentile"],
                "momentum_turn": factors["momentum"]["turn"],
                "sentiment": factors["sentiment"]["state_percentile"],
                "liquidity_tightening": factors["liquidity"]["tightening_percentile"],
                "liquidity_delta_5_sessions": factors["liquidity"]["delta_5_sessions"],
                "action": decision["action"],
                "current_ai_pct": decision["current_ai_pct"],
                "target_ai_pct": decision["target_ai_pct"],
                "rationale": decision.get("rationale") or decision.get("reason"),
                "risk_on_checks": decision.get("risk_on_checks"),
                "risk_off_checks": decision.get("risk_off_checks"),
            }
        )
    generated_at = utc_now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "as_of_date": max(dates),
        "source": "dated AI signal artifacts",
        "source_timestamp": generated_at,
        "data_period": f"{min(dates)}..{max(dates)}",
        "fallback_status": "none" if all(row["official"] for row in rows) else "unverified",
        "replay_type": "point_in_time_event_snapshot_not_statistical_performance_backtest",
        "position_assumption": "each date is evaluated independently from the supplied current_ai_pct",
        "rows": rows,
        "data_audit": {
            "source_files": source_files,
            "requested_date_map": requested_map,
            "future_data_filtered": True,
            "fallback_status": "none" if all(row["official"] for row in rows) else "unverified",
        },
    }
    output_path = AI_BACKTEST_DIR / f"ai_replay_{min(dates)}_{max(dates)}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ai-backtest] {len(rows)} event snapshots -> {output_path}")


if __name__ == "__main__":
    main()
