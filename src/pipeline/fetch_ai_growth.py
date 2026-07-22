#!/usr/bin/env python3
"""Build a point-in-time hyperscaler growth artifact from SEC companyfacts.

Only facts whose SEC ``filed`` date is on or before ``as_of_date`` are eligible.
The adapter extracts discrete-quarter flow facts (roughly 70-110 days) so that
CapEx, operating cash flow and earnings are not mixed with year-to-date values.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import date as date_type, datetime
from typing import Any

import requests

from pipeline_contracts import utc_now_iso
from pipeline_paths import AI_RAW_DIR, ensure_runtime_dirs, validate_as_of_date


SEC_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
COMPANIES = {
    "MSFT": {"cik": 789019, "name": "Microsoft"},
    "AMZN": {"cik": 1018724, "name": "Amazon"},
    "GOOGL": {"cik": 1652044, "name": "Alphabet"},
    "META": {"cik": 1326801, "name": "Meta Platforms"},
    "ORCL": {"cik": 1341439, "name": "Oracle"},
}
METRIC_TAGS = {
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "operating_income": ["OperatingIncomeLoss"],
}
ALLOWED_FORMS = {"10-Q", "10-K", "20-F", "40-F"}
MAX_PERIOD_STALENESS_DAYS = 240


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch point-in-time hyperscaler growth facts from SEC XBRL.")
    parser.add_argument("--date", required=True, help="As-of date in YYYYMMDD format.")
    parser.add_argument(
        "--sec-user-agent",
        default=os.environ.get("ASCLAW_SEC_USER_AGENT", "a-share-claw/0.1 personal investment research"),
        help="SEC-compliant User-Agent. ASCLAW_SEC_USER_AGENT overrides the default.",
    )
    return parser.parse_args()


def _session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.trust_env = os.environ.get("ASCLAW_DATA_TRUST_ENV", "0").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
    return session


def _fetch_companyfacts(session: requests.Session, cik: int) -> dict[str, Any]:
    url = SEC_URL.format(cik=cik)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(url, timeout=90)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or "facts" not in payload:
                raise RuntimeError("SEC returned an unsupported companyfacts payload")
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"SEC companyfacts fetch failed for CIK {cik}: {last_error}")


def _parse_date(value: str) -> date_type:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _metric_records(
    payload: dict[str, Any],
    tags: list[str],
    as_of_date: str,
    *,
    absolute_value: bool = False,
) -> tuple[str | None, list[dict[str, Any]]]:
    cutoff = datetime.strptime(as_of_date, "%Y%m%d").date()
    gaap = payload.get("facts", {}).get("us-gaap", {})
    candidates: list[tuple[date_type, str, list[dict[str, Any]]]] = []
    for tag in tags:
        units = gaap.get(tag, {}).get("units", {})
        raw = units.get("USD", [])
        selected: dict[tuple[str, str], dict[str, Any]] = {}
        for item in raw:
            if item.get("form") not in ALLOWED_FORMS:
                continue
            if not all(item.get(key) for key in ("start", "end", "filed")):
                continue
            start = _parse_date(item["start"])
            end = _parse_date(item["end"])
            filed = _parse_date(item["filed"])
            duration = (end - start).days + 1
            if filed > cutoff or end > cutoff or not 70 <= duration <= 110:
                continue
            value = item.get("val")
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                continue
            normalized = dict(item)
            normalized["val"] = abs(float(value)) if absolute_value else float(value)
            normalized["duration_days"] = duration
            key = (item["start"], item["end"])
            previous = selected.get(key)
            if previous is None or item["filed"] > previous["filed"]:
                selected[key] = normalized
        records = sorted(selected.values(), key=lambda row: (row["end"], row["filed"]))
        pair = _latest_yoy_pair(records)
        if pair is not None:
            candidates.append((_parse_date(pair[0]["end"]), tag, records))
    if not candidates:
        return None, []
    _, tag, records = max(candidates, key=lambda item: item[0])
    return tag, records


def _latest_yoy_pair(records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for current in reversed(records):
        current_end = _parse_date(current["end"])
        candidates: list[tuple[int, dict[str, Any]]] = []
        for prior in records:
            prior_end = _parse_date(prior["end"])
            gap = (current_end - prior_end).days
            if 320 <= gap <= 410:
                candidates.append((abs(gap - 365), prior))
        if candidates:
            return current, min(candidates, key=lambda pair: pair[0])[1]
    return None


def _growth(current: float | None, prior: float | None) -> float | None:
    if current is None or prior in (None, 0):
        return None
    return current / prior - 1.0


def _company_metrics(payload: dict[str, Any], as_of_date: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for metric, tags in METRIC_TAGS.items():
        tag, records = _metric_records(
            payload,
            tags,
            as_of_date,
            absolute_value=metric == "capex",
        )
        pair = _latest_yoy_pair(records)
        if pair is None:
            metrics[metric] = {
                "status": "missing",
                "tag_candidates": tags,
            }
            continue
        current, prior = pair
        staleness_days = (datetime.strptime(as_of_date, "%Y%m%d").date() - _parse_date(current["end"])).days
        if staleness_days > MAX_PERIOD_STALENESS_DAYS:
            metrics[metric] = {
                "status": "missing",
                "reason": "latest_comparable_period_too_stale",
                "tag": tag,
                "latest_comparable_period_end": current["end"],
                "staleness_days": staleness_days,
            }
            continue
        metrics[metric] = {
            "status": "observed",
            "tag": tag,
            "current": current["val"],
            "prior_year": prior["val"],
            "yoy": round(_growth(current["val"], prior["val"]), 6),
            "period_start": current["start"],
            "period_end": current["end"],
            "comparison_period_start": prior["start"],
            "comparison_period_end": prior["end"],
            "filing_date": current["filed"],
            "accession": current.get("accn"),
            "form": current.get("form"),
            "staleness_days": staleness_days,
        }
    capex = metrics.get("capex", {})
    ocf = metrics.get("operating_cash_flow", {})
    if capex.get("status") == "observed" and ocf.get("status") == "observed":
        current_ocf = ocf["current"]
        metrics["funding"] = {
            "capex_to_ocf": round(capex["current"] / current_ocf, 6) if current_ocf else None,
            "free_cash_flow": round(current_ocf - capex["current"], 2),
            "capex_growth_minus_ocf_growth": round(capex["yoy"] - ocf["yoy"], 6),
        }
    else:
        metrics["funding"] = {"status": "missing_capex_or_ocf"}
    return metrics


def _aggregate(companies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for metric in ("capex", "operating_cash_flow", "revenue", "operating_income"):
        rows = [
            company["metrics"][metric]
            for company in companies.values()
            if company["metrics"].get(metric, {}).get("status") == "observed"
        ]
        current = sum(row["current"] for row in rows)
        prior = sum(row["prior_year"] for row in rows)
        output[metric] = {
            "companies_observed": len(rows),
            "current_total_usd": round(current, 2) if rows else None,
            "prior_year_total_usd": round(prior, 2) if rows else None,
            "yoy": round(_growth(current, prior), 6) if rows and prior else None,
            "oldest_period_end": min((row["period_end"] for row in rows), default=None),
            "latest_period_end": max((row["period_end"] for row in rows), default=None),
        }
    paired = [
        company["metrics"]
        for company in companies.values()
        if company["metrics"].get("capex", {}).get("status") == "observed"
        and company["metrics"].get("operating_cash_flow", {}).get("status") == "observed"
    ]
    capex_total = sum(row["capex"]["current"] for row in paired)
    ocf_total = sum(row["operating_cash_flow"]["current"] for row in paired)
    output["funding"] = {
        "companies_observed": len(paired),
        "capex_to_ocf": round(capex_total / ocf_total, 6) if ocf_total else None,
        "free_cash_flow_usd": round(ocf_total - capex_total, 2) if paired else None,
        "capex_growth_minus_ocf_growth": (
            round(output["capex"]["yoy"] - output["operating_cash_flow"]["yoy"], 6)
            if output["capex"]["yoy"] is not None and output["operating_cash_flow"]["yoy"] is not None
            else None
        ),
    }
    return output


def main() -> None:
    args = parse_args()
    date = validate_as_of_date(args.date)
    ensure_runtime_dirs()
    session = _session(args.sec_user_agent)
    companies: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for ticker, spec in COMPANIES.items():
        try:
            payload = _fetch_companyfacts(session, spec["cik"])
            companies[ticker] = {
                "name": spec["name"],
                "cik": spec["cik"],
                "source_url": SEC_URL.format(cik=spec["cik"]),
                "metrics": _company_metrics(payload, date),
            }
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")
    if len(companies) < 3:
        raise SystemExit(f"[ai-growth] fewer than 3 SEC companies available: {'; '.join(errors)}")

    generated_at = utc_now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "as_of_date": date,
        "source": "SEC companyfacts XBRL",
        "source_timestamp": generated_at,
        "data_period": "latest discrete quarter filed on or before as_of_date",
        "fallback_status": "none",
        "point_in_time_filter": "SEC filed date <= as_of_date; period end <= as_of_date; 70-110 day duration",
        "companies": companies,
        "aggregate": _aggregate(companies),
        "omitted_companies": errors,
        "data_audit": {
            "company_count": len(companies),
            "requested_company_count": len(COMPANIES),
            "future_filings_filtered": True,
            "fallback_status": "none",
        },
    }
    output_path = AI_RAW_DIR / f"ai_growth_{date}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ai-growth] {len(companies)}/{len(COMPANIES)} companies -> {output_path}")
    for error in errors:
        print(f"  omitted: {error}")


if __name__ == "__main__":
    main()
