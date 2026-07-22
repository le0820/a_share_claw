#!/usr/bin/env python3
"""fetch_macro.py — 拉取 CN 宏观数据（PMI/CPI/PPI/M2/TSF/零售/LPR）

Usage:
    cd src/pipeline
    uv run python fetch_macro.py [--date YYYYMMDD]
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Optional, Dict, Any

import pandas as pd
import akshare as ak
from pipeline_contracts import utc_now_iso, validate_payload
from pipeline_paths import RAW_DIR, current_date, validate_as_of_date

DATA_DIR = str(RAW_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

def _latest_by_date(df: pd.DataFrame, col: str, date_col: str, as_of_date: str) -> Optional[float]:
    """Extract value from row with most recent date. Sorts by date desc, returns first non-NaN."""
    # Normalize date column: extract year-month from Chinese format
    dates = df[date_col].astype(str)
    # Try formats: "2026年04月份" → 202604, "202604" → 202604, "2026-04-20" → 202604
    parsed = dates.str.extract(r"(\d{4})\D*(\d{1,2})").dropna()
    if parsed.empty:
        return None
    # Create sort key: year*100 + month
    sort_key = parsed[0].astype(int) * 100 + parsed[1].astype(int)
    df = df.assign(_sort=sort_key)
    df = df[df["_sort"] <= int(as_of_date[:6])]
    df = df.sort_values("_sort", ascending=False)
    s = df[col].dropna()
    val = float(s.iloc[0]) if len(s) > 0 else None
    return val

def _latest(df: pd.DataFrame, col: str) -> Optional[float]:
    """Simple last-value extractor for numeric-only indices."""
    s = df[col].dropna()
    return float(s.iloc[-1]) if len(s) > 0 else None


def _latest_by_release_date(df: pd.DataFrame, col: str, date_col: str, as_of_date: str) -> Optional[float]:
    dates = pd.to_datetime(df[date_col], errors="coerce")
    eligible = df.assign(_release_date=dates)
    eligible = eligible[eligible["_release_date"] <= pd.Timestamp(datetime.strptime(as_of_date, "%Y%m%d"))]
    eligible = eligible.sort_values("_release_date")
    values = pd.to_numeric(eligible[col], errors="coerce").dropna()
    return float(values.iloc[-1]) if not values.empty else None

def fetch_all(date_str: str) -> Dict[str, Any]:
    generated_at = utc_now_iso()
    result: Dict[str, Any] = {
        "timestamp": generated_at,
        "as_of_date": date_str,
        "data_date": date_str,
        "source": "akshare 1.18.64 publisher adapters",
        "_meta": {
            "as_of_date": date_str,
            "generated_at": generated_at,
            "source": "akshare 1.18.64 publisher adapters",
            "source_timestamp": generated_at,
            "data_period": f"latest published period <= {date_str[:6]}",
            "fallback_status": "none",
        },
    }
    errors = []

    # ── PMI ──
    try:
        df = ak.macro_china_pmi()
        result["pmi_mfg"] = _latest_by_date(df, "制造业-指数", "月份", date_str)
        result["pmi_non_mfg"] = _latest_by_date(df, "非制造业-指数", "月份", date_str)
        print(f"  PMI 制造业={result['pmi_mfg']}, 非制造={result['pmi_non_mfg']}")
    except Exception as e:
        errors.append(f"PMI: {e}")

    # ── CPI ──
    try:
        df = ak.macro_china_cpi()
        result["cpi_yoy"] = _latest_by_date(df, "全国-同比增长", "月份", date_str)
        print(f"  CPI YoY={result['cpi_yoy']}%")
    except Exception as e:
        errors.append(f"CPI: {e}")

    # ── PPI ──
    try:
        df = ak.macro_china_ppi()
        result["ppi_yoy"] = _latest_by_date(df, "当月同比增长", "月份", date_str)
        print(f"  PPI YoY={result['ppi_yoy']}%")
    except Exception as e:
        errors.append(f"PPI: {e}")

    # ── M2 / M1 ──
    try:
        df = ak.macro_china_money_supply()
        result["m2_yoy"] = _latest_by_date(df, "货币和准货币(M2)-同比增长", "月份", date_str)
        result["m1_yoy"] = _latest_by_date(df, "货币(M1)-同比增长", "月份", date_str)
        print(f"  M2={result['m2_yoy']}%, M1={result['m1_yoy']}%")
    except Exception as e:
        errors.append(f"M2: {e}")

    # ── TSF (社融) ──
    try:
        df = ak.macro_china_shrzgm()
        result["tsf_monthly"] = _latest_by_date(df, "社会融资规模增量", "月份", date_str)
        print(f"  TSF增量={result['tsf_monthly']}")
    except Exception as e:
        errors.append(f"TSF: {e}")

    # ── 零售 ──
    try:
        df = ak.macro_china_consumer_goods_retail()
        result["retail_sales_yoy"] = _latest_by_date(df, "同比增长", "月份", date_str)
        print(f"  零售YoY={result['retail_sales_yoy']}%")
    except Exception as e:
        errors.append(f"零售: {e}")

    # ── LPR ──
    try:
        df = ak.macro_china_lpr()
        df = df.dropna(subset=["LPR1Y", "LPR5Y"])
        result["lpr_1y"] = _latest_by_date(df, "LPR1Y", "TRADE_DATE", date_str)
        result["lpr_5y"] = _latest_by_date(df, "LPR5Y", "TRADE_DATE", date_str)
        print(f"  LPR 1Y={result['lpr_1y']}%, 5Y={result['lpr_5y']}%")
    except Exception as e:
        errors.append(f"LPR: {e}")

    # ── 工业增加值 ──
    try:
        df = ak.macro_china_industrial_production_yoy()
        result["industrial_output_yoy"] = _latest_by_release_date(df, "今值", "日期", date_str)
        print(f"  工业增加值YoY={result['industrial_output_yoy']}%")
    except Exception as e:
        errors.append(f"工业增加值: {e}")

    result["_errors"] = errors
    if errors:
        result["_meta"]["fallback_status"] = "unverified"
    return result

def main():
    parser = argparse.ArgumentParser(description="Fetch CN macro data for an explicit as-of date.")
    parser.add_argument("legacy_date", nargs="?", help="Backward-compatible YYYYMMDD date.")
    parser.add_argument("--date", dest="date", help="As-of date, YYYYMMDD.")
    args = parser.parse_args()
    date_str = args.date or args.legacy_date or current_date().strftime("%Y%m%d")
    try:
        validate_as_of_date(date_str)
    except ValueError as exc:
        parser.error(str(exc))
    out_file = os.path.join(DATA_DIR, f"raw_macro_{date_str}.json")
    today = current_date().strftime("%Y%m%d")
    if date_str < today:
        if not os.path.exists(out_file):
            raise SystemExit(
                f"[fetch_macro] historical live fetch is forbidden for {date_str}; "
                f"restore the archived exact artifact {out_file}"
            )
        try:
            with open(out_file, encoding="utf-8") as existing_file:
                existing = json.load(existing_file)
            validate_payload(existing, date_str, label="CN macro archive", path=out_file)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"[fetch_macro] invalid historical archive: {exc}") from exc
        print(f"[fetch_macro] historical exact artifact reused -> {out_file}")
        return

    print(f"[fetch_macro] 拉取 CN 宏观数据 (data_date={date_str})...")

    data = fetch_all(date_str)

    with open(out_file, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    n_ok = sum(1 for k, v in data.items() if v is not None and not k.startswith("_"))
    n_err = len(data["_errors"])
    print(f"\n[fetch_macro] 完成: {n_ok} 项, {n_err} 失败 → {out_file}")
    if n_err:
        for e in data["_errors"]:
            print(f"  ⚠️ {e}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
