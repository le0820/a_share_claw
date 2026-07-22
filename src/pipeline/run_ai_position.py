#!/usr/bin/env python3
"""Apply the AI position state machine to an exact dated factor artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_strategy import position_decision
from pipeline_contracts import fallback_status, utc_now_iso, validate_payload
from pipeline_paths import AI_FACTORS_DIR, AI_SIGNALS_DIR, STATE_FILE, ensure_runtime_dirs, validate_as_of_date


RULES_PATH = Path(__file__).resolve().parents[1] / "compiled" / "ai_strategy_rules.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an AI allocation signal from exact dated factors.")
    parser.add_argument("--date", required=True, help="As-of date in YYYYMMDD format.")
    parser.add_argument("--current-ai-pct", type=float, default=57.5, help="Current AI allocation as percent of NAV.")
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="Research only: allow an unverified historical factor artifact; system_state is not updated.",
    )
    return parser.parse_args()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"missing input: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"input is not a JSON object: {path}")
    return payload


def main() -> None:
    args = parse_args()
    date = validate_as_of_date(args.date)
    if not 0 <= args.current_ai_pct <= 100:
        raise SystemExit("--current-ai-pct must be between 0 and 100")
    ensure_runtime_dirs()
    rules = _load(RULES_PATH)
    factor_path = AI_FACTORS_DIR / f"ai_factors_{date}.json"
    factor_payload = _load(factor_path)
    unverified = fallback_status(factor_payload) == "unverified"
    if unverified and not args.allow_unverified:
        raise SystemExit("factor artifact is unverified; pass --allow-unverified only for a research replay")
    validate_payload(
        factor_payload,
        date,
        allow_static=args.allow_unverified,
        label="AI factors",
        path=factor_path,
    )
    decision = position_decision(factor_payload["factors"], rules, args.current_ai_pct)
    generated_at = utc_now_iso()
    official = bool(factor_payload.get("official")) and not unverified
    payload = {
        "schema_version": 1,
        "methodology_version": rules["version"],
        "generated_at": generated_at,
        "as_of_date": date,
        "effective_trade_date": factor_payload["effective_trade_date"],
        "source": "AI position state machine",
        "source_timestamp": generated_at,
        "fallback_status": "none" if official else "unverified",
        "official": official,
        "decision": decision,
        "factor_summary": factor_payload["factors"],
        "execution_contract": {
            "position_denominator": "AI assets as percent of total investable NAV",
            "normal_trade_step_pct": rules["tactical"]["position"]["step_pct"],
            "execute_no_earlier_than": "next A-share session after the recorded signal cutoff",
            "repeated_signal_rule": "do not repeat an order when the target allocation was already reached",
        },
        "data_audit": {
            "source_files": [str(factor_path), str(RULES_PATH)],
            "fallback_status": "none" if official else "unverified",
            "official": official,
        },
    }
    output_path = AI_SIGNALS_DIR / f"ai_signal_{date}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if official:
        state = _load(STATE_FILE) if STATE_FILE.is_file() else {}
        previous_date = state.get("ai_strategy", {}).get("as_of_date")
        if not isinstance(previous_date, str) or previous_date <= date:
            state["ai_strategy"] = {
                "as_of_date": date,
                "effective_trade_date": payload["effective_trade_date"],
                "methodology_version": rules["version"],
                "growth_regime": payload["factor_summary"]["growth"]["regime"],
                "action": decision["action"],
                "current_ai_pct": decision["current_ai_pct"],
                "target_ai_pct": decision["target_ai_pct"],
                "source_file": str(output_path),
                "fallback_status": "none",
                "last_updated": generated_at,
            }
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[ai-position] {date}/{payload['effective_trade_date']}: {decision['action']} "
        f"{decision['current_ai_pct']}%->{decision['target_ai_pct']}% -> {output_path}"
    )


if __name__ == "__main__":
    main()
