#!/usr/bin/env python3
"""Archive current GPU-instance and token list-price snapshots.

Historical dates are never backfilled from today's price catalog. Price growth
is emitted only after the workspace has at least two dated snapshots.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
from statistics import median
from typing import Any

import requests

from pipeline_contracts import utc_now_iso, validate_payload
from pipeline_paths import AI_RAW_DIR, current_date, ensure_runtime_dirs, validate_as_of_date


OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
AZURE_URL = "https://prices.azure.com/api/retail/prices"
FRONTIER_PATTERNS = ("gpt-5", "claude-opus", "claude-sonnet", "gemini-2.5-pro", "gemini-3")
COMMODITY_PATTERNS = ("mini", "flash", "haiku", "small", "nano")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive current AI GPU and token price snapshots.")
    parser.add_argument("--date", required=True, help="Snapshot date in YYYYMMDD format.")
    parser.add_argument("--azure-region", default="eastus", help="Azure region (default: eastus).")
    return parser.parse_args()


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = os.environ.get("ASCLAW_DATA_TRUST_ENV", "0").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    session.headers.update({"User-Agent": "a-share-claw/0.1 personal investment research"})
    return session


def _price_per_million(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number * 1_000_000


def fetch_token_prices(session: requests.Session) -> dict[str, Any]:
    headers = {}
    token = os.environ.get("OPENROUTER_API_KEY")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = session.get(OPENROUTER_URL, headers=headers, timeout=60)
    response.raise_for_status()
    rows: list[dict[str, Any]] = []
    for model in response.json().get("data", []):
        pricing = model.get("pricing") or {}
        prompt = _price_per_million(pricing.get("prompt"))
        completion = _price_per_million(pricing.get("completion"))
        if prompt is None or completion is None:
            continue
        rows.append(
            {
                "model_id": model.get("id"),
                "name": model.get("name"),
                "prompt_usd_per_million": round(prompt, 6),
                "completion_usd_per_million": round(completion, 6),
            }
        )

    def basket(patterns: tuple[str, ...]) -> dict[str, Any]:
        selected = [
            row
            for row in rows
            if any(pattern in str(row["model_id"]).casefold() for pattern in patterns)
        ]
        return {
            "model_count": len(selected),
            "models": [row["model_id"] for row in selected],
            "median_prompt_usd_per_million": (
                round(median(row["prompt_usd_per_million"] for row in selected), 6) if selected else None
            ),
            "median_completion_usd_per_million": (
                round(median(row["completion_usd_per_million"] for row in selected), 6) if selected else None
            ),
        }

    return {
        "source": "OpenRouter models API; provider official price pages remain the preferred cross-check",
        "source_url": OPENROUTER_URL,
        "raw_model_count": len(rows),
        "frontier_basket": basket(FRONTIER_PATTERNS),
        "commodity_basket": basket(COMMODITY_PATTERNS),
    }


def fetch_azure_gpu_prices(session: requests.Session, region: str) -> dict[str, Any]:
    url: str | None = AZURE_URL
    params: dict[str, str] | None = {
        "$filter": f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}' and priceType eq 'Consumption'"
    }
    offers: list[dict[str, Any]] = []
    pages = 0
    while url and pages < 20:
        response = session.get(url, params=params, timeout=90)
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("Items", []):
            sku = str(item.get("armSkuName") or "")
            product = str(item.get("productName") or "")
            if not sku.upper().startswith(("STANDARD_NC", "STANDARD_ND", "STANDARD_NV")):
                continue
            if "Windows" in product or item.get("unitOfMeasure") != "1 Hour":
                continue
            offers.append(
                {
                    "arm_sku_name": sku,
                    "product_name": product,
                    "meter_name": item.get("meterName"),
                    "region": item.get("armRegionName"),
                    "currency": item.get("currencyCode"),
                    "retail_price_per_instance_hour": item.get("retailPrice"),
                    "effective_start_date": item.get("effectiveStartDate"),
                }
            )
        url = payload.get("NextPageLink")
        params = None
        pages += 1
    by_sku: dict[str, list[float]] = {}
    for offer in offers:
        value = offer.get("retail_price_per_instance_hour")
        if isinstance(value, (int, float)):
            by_sku.setdefault(offer["arm_sku_name"], []).append(float(value))
    summary = [
        {
            "arm_sku_name": sku,
            "median_price_per_instance_hour": round(median(values), 6),
            "offer_count": len(values),
        }
        for sku, values in sorted(by_sku.items())
    ]
    return {
        "source": "Azure Retail Prices API",
        "source_url": AZURE_URL,
        "region": region,
        "normalization_status": "per-instance only; GPU count/SKU quality mapping required before factor scoring",
        "offers": offers,
        "sku_summary": summary,
    }


def _previous_snapshot(date: str) -> dict[str, Any] | None:
    candidates: list[tuple[str, Path]] = []
    for raw_path in glob.glob(str(AI_RAW_DIR / "ai_prices_*.json")):
        path = Path(raw_path)
        file_date = path.stem.rsplit("_", 1)[-1]
        if len(file_date) == 8 and file_date < date:
            candidates.append((file_date, path))
    if not candidates:
        return None
    previous_date, path = sorted(candidates)[-1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {"date": previous_date, "path": str(path), "payload": payload}


def _basket_growth(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if previous is None:
        return {"status": "insufficient_history", "previous_snapshot": None}
    result: dict[str, Any] = {"status": "observed", "previous_snapshot": previous["date"], "baskets": {}}
    prior_tokens = previous["payload"].get("token_prices", {})
    for basket_name in ("frontier_basket", "commodity_basket"):
        current_basket = current.get(basket_name, {})
        prior_basket = prior_tokens.get(basket_name, {})
        basket_result: dict[str, Any] = {}
        for price_name in ("median_prompt_usd_per_million", "median_completion_usd_per_million"):
            current_price = current_basket.get(price_name)
            prior_price = prior_basket.get(price_name)
            basket_result[f"{price_name}_growth"] = (
                round(current_price / prior_price - 1, 6)
                if isinstance(current_price, (int, float)) and isinstance(prior_price, (int, float)) and prior_price
                else None
            )
        result["baskets"][basket_name] = basket_result
    return result


def main() -> None:
    args = parse_args()
    date = validate_as_of_date(args.date)
    ensure_runtime_dirs()
    output_path = AI_RAW_DIR / f"ai_prices_{date}.json"
    today = current_date().strftime("%Y%m%d")
    if date < today:
        if not output_path.is_file():
            raise SystemExit(
                f"[ai-prices] historical price catalogs cannot be reconstructed; exact snapshot missing: {output_path}"
            )
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        validate_payload(payload, date, label="AI price archive", path=output_path)
        print(f"[ai-prices] reused historical snapshot -> {output_path}")
        return

    session = _session()
    errors: list[str] = []
    try:
        token_prices = fetch_token_prices(session)
    except Exception as exc:
        token_prices = {"status": "unavailable"}
        errors.append(f"token_prices: {exc}")
    try:
        gpu_prices = fetch_azure_gpu_prices(session, args.azure_region)
    except Exception as exc:
        gpu_prices = {"status": "unavailable"}
        errors.append(f"gpu_prices: {exc}")
    if token_prices.get("status") == "unavailable" and gpu_prices.get("status") == "unavailable":
        raise SystemExit(f"[ai-prices] all snapshot providers failed: {'; '.join(errors)}")

    previous = _previous_snapshot(date)
    generated_at = utc_now_iso()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "as_of_date": date,
        "source": "Azure Retail Prices API and OpenRouter models API",
        "source_timestamp": generated_at,
        "data_period": "current catalog snapshot",
        "fallback_status": "none",
        "gpu_prices": gpu_prices,
        "token_prices": token_prices,
        "token_price_growth": _basket_growth(token_prices, previous),
        "provider_errors": errors,
        "data_audit": {
            "snapshot_only": True,
            "historical_backfill_forbidden": True,
            "fallback_status": "none",
        },
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ai-prices] current catalog snapshot -> {output_path}")


if __name__ == "__main__":
    main()
