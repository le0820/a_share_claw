#!/usr/bin/env python3
"""Fetch the macro series used by the AI tactical liquidity state.

Official historical runs require an already archived vintage. The explicit
``--allow-current-vintage-backtest`` mode is research-only and is labelled
``unverified`` so it cannot silently become an official action artifact.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import zipfile
from datetime import datetime, timedelta
from typing import Any
from xml.etree import ElementTree

import requests

from pipeline_contracts import utc_now_iso, validate_payload
from pipeline_paths import AI_RAW_DIR, current_date, ensure_runtime_dirs, validate_as_of_date


FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
TREASURY_XML_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
)
TREASURY_DATASETS = {
    "daily_treasury_yield_curve": {
        "ust_2y": "BC_2YEAR",
        "ust_10y": "BC_10YEAR",
        "ust_30y": "BC_30YEAR",
    },
    "daily_treasury_real_yield_curve": {
        "tips_5y": "TC_5YEAR",
        "tips_10y": "TC_10YEAR",
    },
}
SERIES = {
    "vix": {"id": "VIXCLS", "frequency": "daily", "lag_days": 1},
    "ust_2y": {"id": "DGS2", "frequency": "daily", "lag_days": 1},
    "ust_10y": {"id": "DGS10", "frequency": "daily", "lag_days": 1},
    "ust_30y": {"id": "DGS30", "frequency": "daily", "lag_days": 1},
    "tips_5y": {"id": "DFII5", "frequency": "daily", "lag_days": 1},
    "tips_10y": {"id": "DFII10", "frequency": "daily", "lag_days": 1},
    "breakeven_5y": {"id": "T5YIE", "frequency": "daily", "lag_days": 1},
    "breakeven_10y": {"id": "T10YIE", "frequency": "daily", "lag_days": 1},
    "broad_dollar": {"id": "DTWEXBGS", "frequency": "daily", "lag_days": 2},
    "credit_spread": {"id": "BAA10Y", "frequency": "daily", "lag_days": 1},
    "brent": {"id": "DCOILBRENTEU", "frequency": "daily", "lag_days": 1},
    "nfci": {"id": "NFCI", "frequency": "weekly", "lag_days": 7},
    "cpi": {"id": "CPIAUCSL", "frequency": "monthly", "lag_days": 45},
    "core_cpi": {"id": "CPILFESL", "frequency": "monthly", "lag_days": 45},
    "ppi_final_demand": {"id": "PPIFIS", "frequency": "monthly", "lag_days": 45},
    "pce": {"id": "PCEPI", "frequency": "monthly", "lag_days": 60},
    "core_pce": {"id": "PCEPILFE", "frequency": "monthly", "lag_days": 60},
}
CORE_SERIES = {
    "vix", "ust_2y", "ust_10y", "ust_30y", "tips_10y", "broad_dollar", "credit_spread", "brent"
}
CHINA_SERIES = {
    "china_cpi_yoy": {
        "fetcher": "macro_china_cpi",
        "date_column": "月份",
        "value_column": "全国-同比增长",
    },
    "china_ppi_yoy": {
        "fetcher": "macro_china_ppi",
        "date_column": "月份",
        "value_column": "当月同比增长",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch AI tactical macro histories from FRED.")
    parser.add_argument("--date", required=True, help="A-share as-of date in YYYYMMDD format.")
    parser.add_argument(
        "--allow-current-vintage-backtest",
        action="store_true",
        help="Research only: fetch today's FRED vintage for a historical date and label it unverified.",
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
    session.headers.update({"User-Agent": "a-share-claw/0.1 personal investment research"})
    return session


def _fred_export_texts(content: bytes) -> list[str]:
    if content.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            csv_names = [name for name in archive.namelist() if name.casefold().endswith(".csv")]
            if not csv_names:
                raise RuntimeError("FRED multi-series ZIP contains no CSV")
            return [archive.read(name).decode("utf-8-sig") for name in csv_names]
    return [content.decode("utf-8-sig")]


def _download_fred_export(series_ids: list[str]) -> bytes:
    url = FRED_URL.format(series_id=",".join(series_ids))
    # A single curl export is markedly more reliable than a burst of urllib3
    # graph requests on macOS. It remains the same official FRED URL/vintage.
    curl_args = ["curl"]
    if not _session().trust_env:
        curl_args.extend(["--noproxy", "*"])
    curl_args.extend(
        [
            "-L",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "2",
            "--retry-all-errors",
            "--retry-delay",
            "1",
            "--max-time",
            "45",
            url,
        ]
    )
    proc = subprocess.run(
        curl_args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode == 0:
        content = proc.stdout
    else:
        try:
            response = _session().get(url, timeout=(10, 30))
            response.raise_for_status()
            content = response.content
        except requests.RequestException as exc:
            raise RuntimeError(
                f"FRED curl and requests export failed: {proc.stderr.decode(errors='replace')[-500:]}; {exc}"
            ) from exc
    return content


def _fetch_all_fred_rows(series_ids: list[str]) -> dict[str, list[tuple[str, float]]]:
    output: dict[str, list[tuple[str, float]]] = {series_id: [] for series_id in series_ids}
    # FRED graph export silently drops series after its per-chart limit. Keep
    # each request below that limit and merge the official CSV groups.
    for start in range(0, len(series_ids), 10):
        batch = series_ids[start : start + 10]
        content = _download_fred_export(batch)
        for text in _fred_export_texts(content):
            reader = csv.DictReader(io.StringIO(text))
            for raw in reader:
                observation_date = raw.get("observation_date") or raw.get("DATE") or raw.get("date")
                if not observation_date:
                    continue
                for series_id in batch:
                    value = str(raw.get(series_id, "")).strip()
                    if value in {"", ".", "NA"}:
                        continue
                    try:
                        output[series_id].append((observation_date, float(value)))
                    except ValueError:
                        continue
    return output


def _fetch_treasury_rows(
    dataset: str,
    field_map: dict[str, str],
    year: int,
) -> tuple[dict[str, list[tuple[str, float]]], str | None]:
    response = _session().get(
        TREASURY_XML_URL,
        params={"data": dataset, "field_tdr_date_value": str(year)},
        timeout=(10, 45),
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "metadata": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    }
    output: dict[str, list[tuple[str, float]]] = {name: [] for name in field_map}
    for entry in root.findall("atom:entry", namespaces):
        properties = entry.find("atom:content/metadata:properties", namespaces)
        if properties is None:
            continue
        values = {child.tag.split("}")[-1]: child.text for child in properties}
        observation_date = str(values.get("NEW_DATE") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", observation_date):
            continue
        for name, field in field_map.items():
            value = _number(values.get(field))
            if value is not None:
                output[name].append((observation_date, value))
    updated = root.findtext("atom:updated", default=None, namespaces=namespaces)
    return output, updated


def _append_newer_observations(
    base: list[dict[str, Any]],
    supplement: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    latest_base = base[-1]["observation_date"] if base else ""
    additions = [row for row in supplement if row["observation_date"] > latest_base]
    if not additions:
        return base, False
    combined = {row["observation_date"]: row for row in [*base, *additions]}
    return [combined[key] for key in sorted(combined)], True


def _eligible_rows(
    rows: list[tuple[str, float]],
    target: datetime,
    lag_days: int,
) -> tuple[list[dict[str, Any]], str]:
    availability_cutoff = (target - timedelta(days=lag_days)).date()
    history_start = target.date() - timedelta(days=365 * 8)
    output = [
        {"observation_date": observation_date, "value": round(value, 8)}
        for observation_date, value in rows
        if history_start <= datetime.strptime(observation_date, "%Y-%m-%d").date() <= availability_cutoff
    ]
    return output, availability_cutoff.isoformat()


def _month_start(value: Any) -> datetime | None:
    match = re.search(r"(\d{4})\D*(\d{1,2})", str(value))
    if match is None:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), 1)
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _fetch_china_history(spec: dict[str, str], target: datetime) -> tuple[list[dict[str, Any]], str]:
    import akshare as ak

    frame = getattr(ak, spec["fetcher"])()
    date_column = spec["date_column"]
    value_column = spec["value_column"]
    if date_column not in frame.columns or value_column not in frame.columns:
        raise RuntimeError(
            f"AkShare response missing {date_column}/{value_column}; columns={list(frame.columns)}"
        )
    availability_cutoff = (target - timedelta(days=45)).date()
    history_start = target.date() - timedelta(days=365 * 8)
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        month = _month_start(row[date_column])
        value = _number(row[value_column])
        if month is None or value is None or not history_start <= month.date() <= availability_cutoff:
            continue
        rows.append({"observation_date": month.strftime("%Y-%m-%d"), "value": round(value, 8)})
    unique = {row["observation_date"]: row for row in rows}
    return [unique[key] for key in sorted(unique)], availability_cutoff.isoformat()


def main() -> None:
    args = parse_args()
    date = validate_as_of_date(args.date)
    ensure_runtime_dirs()
    output_path = AI_RAW_DIR / f"ai_macro_{date}.json"
    today = current_date().strftime("%Y%m%d")
    historical = date < today
    if historical and not args.allow_current_vintage_backtest:
        if not output_path.is_file():
            raise SystemExit(
                f"[ai-macro] historical vintage required for {date}; restore {output_path} or explicitly "
                "use --allow-current-vintage-backtest for a labelled research replay"
            )
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        validate_payload(existing, date, allow_static=True, label="AI macro archive", path=output_path)
        print(f"[ai-macro] reused exact archived artifact -> {output_path}")
        return

    target = datetime.strptime(date, "%Y%m%d")
    histories: dict[str, Any] = {}
    errors: list[str] = []
    treasury_rows: dict[str, list[tuple[str, float]]] = {}
    treasury_feed_updated: dict[str, str | None] = {}
    for dataset, field_map in TREASURY_DATASETS.items():
        try:
            rows, updated = _fetch_treasury_rows(dataset, field_map, target.year)
            treasury_rows.update(rows)
            treasury_feed_updated[dataset] = updated
        except Exception as exc:
            errors.append(f"treasury/{dataset}: {exc}")
    try:
        fred_rows = _fetch_all_fred_rows([spec["id"] for spec in SERIES.values()])
    except Exception as exc:
        fred_rows = {}
        errors.append(f"fred_multi_export: {exc}")
    for name, spec in SERIES.items():
        try:
            rows = fred_rows.get(spec["id"], [])
            eligible, cutoff = _eligible_rows(rows, target, spec["lag_days"])
            latest_source = "FRED graph CSV"
            supplemented = False
            if name in treasury_rows:
                treasury_eligible, _ = _eligible_rows(treasury_rows[name], target, spec["lag_days"])
                eligible, supplemented = _append_newer_observations(eligible, treasury_eligible)
                if supplemented:
                    latest_source = "U.S. Treasury Daily Treasury Rates XML"
            if not eligible:
                raise RuntimeError("no observation available before the conservative availability cutoff")
            histories[name] = {
                "series": spec["id"],
                "frequency": spec["frequency"],
                "availability_lag_days": spec["lag_days"],
                "availability_cutoff": cutoff,
                "latest_observation_date": eligible[-1]["observation_date"],
                "latest_value": eligible[-1]["value"],
                "latest_observation_source": latest_source,
                "treasury_supplemented": supplemented,
                "observations": eligible,
            }
        except Exception as exc:
            errors.append(f"{name}/{spec['id']}: {exc}")
    for name, spec in CHINA_SERIES.items():
        try:
            eligible, cutoff = _fetch_china_history(spec, target)
            if not eligible:
                raise RuntimeError("no observation available before the conservative availability cutoff")
            histories[name] = {
                "series": spec["fetcher"],
                "publisher": "National Bureau of Statistics of China via AkShare adapter",
                "frequency": "monthly",
                "availability_lag_days": 45,
                "availability_cutoff": cutoff,
                "latest_observation_date": eligible[-1]["observation_date"],
                "latest_value": eligible[-1]["value"],
                "observations": eligible,
            }
        except Exception as exc:
            errors.append(f"{name}/{spec['fetcher']}: {exc}")
    missing_core = sorted(CORE_SERIES - set(histories))
    if missing_core:
        raise SystemExit(f"[ai-macro] missing core series: {', '.join(missing_core)}; {'; '.join(errors)}")

    generated_at = utc_now_iso()
    fallback = "unverified" if historical else "none"
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "as_of_date": date,
        "source": "FRED graph CSV; U.S. Treasury Daily Treasury Rates XML; NBS China series via AkShare adapter",
        "source_timestamp": generated_at,
        "data_period": "history through a conservative per-frequency availability cutoff",
        "fallback_status": fallback,
        "official": not historical,
        "vintage_status": "current_vintage_research_backtest" if historical else "current_date_current_vintage",
        "exact_dxy_status": "omitted; broad_dollar is DTWEXBGS and is not relabelled DXY",
        "source_urls": {
            "fred": FRED_URL,
            "us_treasury": TREASURY_XML_URL,
        },
        "histories": histories,
        "omitted_or_failed": errors,
        "data_audit": {
            "future_observations_filtered": True,
            "monthly_release_safety": "conservative lag; no fabricated release_date",
            "treasury_feed_updated": treasury_feed_updated,
            "treasury_supplemented_series": sorted(
                name for name, history in histories.items() if history.get("treasury_supplemented")
            ),
            "fallback_status": fallback,
            "official": not historical,
        },
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[ai-macro] {len(histories)}/{len(SERIES) + len(CHINA_SERIES)} series "
        f"-> {output_path} ({fallback})"
    )
    for error in errors:
        print(f"  omitted: {error}")


if __name__ == "__main__":
    main()
