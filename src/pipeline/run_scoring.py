#!/usr/bin/env python3
"""run_scoring.py — Phase 2 scoring: generate L1/L2/L3/composite JSON from dated inputs.

Output: data/scores/scores_*.json, update data/state/system_state.json
"""

import argparse
import glob
import os
import sys
import json
import re
from datetime import datetime, timezone
from pathlib import Path

# 本地 L1 规则（纯函数 + 权重 + tech_capex 映射），见 rules_L1.py
from rules_L1 import (
    US_WEIGHTS, CN_WEIGHTS, L1_US_WEIGHT, L1_CN_WEIGHT,
    score_us_employment, score_us_inflation, score_us_monetary,
    score_us_financial, score_us_tech_capex,
    score_cn_manufacturing, score_cn_consumption, score_cn_credit,
    factor_coverage, us_composite as _us_composite_fn, cn_composite as _cn_composite_fn,
    l1_composite as _l1_composite_fn,
)
from pipeline_contracts import contains_static_marker, embedded_date, fallback_status, utc_now_iso, validate_payload
from pipeline_paths import ANALYSIS_DIR, RAW_DIR, SCORES_DIR, STATE_FILE, current_date, validate_as_of_date
from pipeline_universe import market_csv_path, scoring_assets

BASE = os.path.dirname(__file__)
OUT_SCORES = str(SCORES_DIR)
OUT_DATA = str(RAW_DIR)
OUT_STATE = str(STATE_FILE)
WEIGHT_MATRIX_PATH = Path(__file__).resolve().parents[1] / "compiled" / "weight_matrix.json"
with WEIGHT_MATRIX_PATH.open(encoding="utf-8") as _weight_file:
    WEIGHT_MATRIX = json.load(_weight_file)
SCORING_METHODOLOGY = WEIGHT_MATRIX["_version"]
COMPOSITE_WEIGHTS = WEIGHT_MATRIX["composite_weights"]
L3_WEIGHTS = WEIGHT_MATRIX["L3_weights"]

def _parse_args():
    parser = argparse.ArgumentParser(
        description="Generate L1/L2/L3/composite scores for an explicit as-of date."
    )
    parser.add_argument("legacy_date", nargs="?", help="Backward-compatible YYYYMMDD date.")
    parser.add_argument("--date", dest="date", help="As-of date, YYYYMMDD. Required for official runs.")
    parser.add_argument(
        "--allow-stale-fallback",
        "--allow-latest-fallback",
        action="store_true",
        dest="allow_stale_fallback",
        help="Allow fallback to the newest available file whose date is <= --date. Future files are never used.",
    )
    parser.add_argument(
        "--allow-static-fallback",
        action="store_true",
        help="Allow embedded static fallback values for missing US/web indicators. Do not use for official reports.",
    )
    args = parser.parse_args()
    date = args.date or args.legacy_date or current_date().strftime("%Y%m%d")
    try:
        validate_as_of_date(date)
    except ValueError as exc:
        parser.error(str(exc))
    args.date = date
    return args

ARGS = _parse_args()
DATE = ARGS.date
DATA_AUDIT = {
    "as_of_date": DATE,
    "allow_stale_fallback": ARGS.allow_stale_fallback,
    "allow_static_fallback": ARGS.allow_static_fallback,
    "source_files": [],
    "fallbacks": [],
    "static_fallback_fields": [],
    "omitted_factors": [],
    "omitted_layers": ["L2"],
    "quality_notes": [],
}

def _date_from_filename(path, prefix):
    name = os.path.basename(path)
    match = re.fullmatch(rf"{re.escape(prefix)}_(\d{{8}})\.json", name)
    return match.group(1) if match else None

def _record_source(label, path, role="exact"):
    DATA_AUDIT["source_files"].append({
        "label": label,
        "path": os.path.abspath(path),
        "role": role,
    })

def _embedded_date(data):
    return embedded_date(data) if isinstance(data, dict) else None

def _embedded_fallback_status(data):
    return fallback_status(data) if isinstance(data, dict) else "none"

def _contains_static_fallback(data):
    return contains_static_marker(data)

def _validate_loaded_payload(label, data, requested_date, path):
    try:
        validate_payload(
            data,
            requested_date,
            allow_stale=ARGS.allow_stale_fallback,
            allow_static=ARGS.allow_static_fallback,
            label=label,
            path=path,
        )
    except ValueError as exc:
        raise SystemExit(f"  ❌ {exc}") from exc

    payload_status = _embedded_fallback_status(data)
    if payload_status == "stale_fallback":
        if not ARGS.allow_stale_fallback:
            raise SystemExit(f"  ❌ {label}: stale fallback is present but --allow-stale-fallback was not provided: {path}")
        DATA_AUDIT["fallbacks"].append({"label": label, "requested": requested_date, "path": os.path.abspath(path), "status": payload_status})
    elif payload_status in {"static_fallback", "unverified"}:
        if not ARGS.allow_static_fallback:
            raise SystemExit(f"  ❌ {label}: {payload_status} is not allowed for an official run: {path}")
        DATA_AUDIT["static_fallback_fields"].append(label)

def _load_json_for_date(directory, prefix, date, label, required=True):
    exact = os.path.join(directory, f"{prefix}_{date}.json")
    if os.path.exists(exact):
        _record_source(label, exact, "exact")
        with open(exact) as f:
            data = json.load(f)
        _validate_loaded_payload(label, data, date, exact)
        return data, exact

    if not ARGS.allow_stale_fallback:
        msg = f"{label}: expected {exact}, but it does not exist. Re-run upstream with --date {date} or pass --allow-stale-fallback."
        if required:
            raise SystemExit(f"  ❌ {msg}")
        print(f"  ⚠️ {msg}")
        return None, None

    candidates = []
    for path in glob.glob(os.path.join(directory, f"{prefix}_*.json")):
        fdate = _date_from_filename(path, prefix)
        if fdate and fdate <= date:
            candidates.append((fdate, path))
    if not candidates:
        msg = f"{label}: no {prefix}_*.json file with date <= {date}."
        if required:
            raise SystemExit(f"  ❌ {msg}")
        print(f"  ⚠️ {msg}")
        return None, None

    fallback_date, fallback_path = sorted(candidates)[-1]
    DATA_AUDIT["fallbacks"].append({
        "label": label,
        "requested": date,
        "used": fallback_date,
        "path": os.path.abspath(fallback_path),
    })
    _record_source(label, fallback_path, "stale_fallback")
    print(f"  ⚠️ {label}: {prefix}_{date}.json missing, using {os.path.basename(fallback_path)}")
    with open(fallback_path) as f:
        data = json.load(f)
    _validate_loaded_payload(label, data, date, fallback_path)
    return data, fallback_path

# ── RSI(14) calculation ───────────────────────────────────
def calc_rsi_14(csv_path, as_of_date):
    """从easy-tdx导出的CSV计算RSI(14)"""
    try:
        import pandas as pd
        import numpy as np
        df = pd.read_csv(csv_path)
        if 'close' not in df.columns or 'trade_date' not in df.columns:
            return None
        df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce')
        cutoff = pd.Timestamp(datetime.strptime(as_of_date, "%Y%m%d"))
        df = df[df['trade_date'] <= cutoff].sort_values('trade_date')
        if len(df) < 15:
            return None
        close = df['close'].values
        delta = np.diff(close)
        gains = np.where(delta > 0, delta, 0)
        losses = np.where(delta < 0, -delta, 0)
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(float(rsi), 1)
    except Exception as e:
        return None

RSI_SYMBOLS = {symbol: str(market_csv_path(symbol)) for symbol in scoring_assets()}
rsi_values = {}
for sym, path in RSI_SYMBOLS.items():
    r = calc_rsi_14(path, DATE)
    if r is not None:
        rsi_values[sym] = r

# ── Load data ──────────────────────────────────────────────
macro, MACRO_PATH = _load_json_for_date(OUT_DATA, "raw_macro", DATE, "CN macro data")
_required_cn_fields = ["pmi_mfg", "pmi_non_mfg", "retail_sales_yoy", "m2_yoy", "m1_yoy"]
_missing_cn_fields = [field for field in _required_cn_fields if macro.get(field) is None]
if _missing_cn_fields:
    raise SystemExit(f"  ❌ Missing CN macro fields for {DATE}: {', '.join(_missing_cn_fields)}")
if macro.get("tsf_monthly") is None:
    DATA_AUDIT["quality_notes"].append(
        "L1.CN.credit: tsf_monthly unavailable; credit score uses observed M2/M1 only"
    )

def _load_analysis(base_name, date):
    data, _ = _load_json_for_date(str(ANALYSIS_DIR), base_name, date, f"{base_name} analysis")
    return data

p1 = _load_analysis("p1_upgrade_results", DATE)

# ── VALUE_MAP: loads from raw_macro_us_{DATE}.json if available, else static defaults ──
VALUE_MAP = {
    "vix": None, "spx": None, "ust_10y": None, "ust_30y": None, "ust_2y": None,
    "aaii_bull": None, "aaii_bear": None, "aaii_spread": None,
    "cpi_us_yoy": None, "core_cpi_yoy": None, "cpi_us_mom": None,
    "ppi_us_yoy": None, "ppi_us_mom": None,
    "fed_rate": None, "nfp_actual": None, "nfp_consensus": None,
    "unemployment": None, "brent": None, "dxy": None,
    "put_call_ratio": None, "credit_spread": None, "credit_spread_change_20d": None,
    "jp_jgb_10y": None, "usdjpy": None,
}

_us, _US_PATH = _load_json_for_date(OUT_DATA, "raw_macro_us", DATE, "US/web macro data", required=False)
if _us:
    for _k in VALUE_MAP:
        if _k in _us and _us[_k] is not None:
            VALUE_MAP[_k] = _us[_k]

# Missing fields stay None. Optional factors are omitted and observed weights are
# renormalized; no neutral score is injected.

# 仅要求有干净官方来源（FRED）的字段；以下字段无免 key 干净来源，已不再获取：
#   dxy, put_call_ratio, nfp_consensus
_required_value_fields = [
    "vix", "ust_10y", "ust_30y", "brent",
    "cpi_us_yoy", "core_cpi_yoy", "ppi_us_yoy", "fed_rate", "nfp_actual",
    "unemployment", "credit_spread", "jp_jgb_10y", "usdjpy"
]
_missing_required = [k for k in _required_value_fields if VALUE_MAP.get(k) is None]
if _missing_required:
    raise SystemExit(
        "  ❌ Missing US/web indicator fields for "
        f"{DATE}: {', '.join(_missing_required)}. "
        f"Populate raw_macro_us_{DATE}.json from dated sources; official scoring does not synthesize them."
    )

# ── L1 Scoring (DATA-DRIVEN, 2026-07-09 重构) ──────────────
# 所有 score_* 阈值函数与权重已抽至 rules_L1.py（数据驱动, 阈值版 v1, 待回测校准）。
# 本块仅做「取数 → 调 rules_L1 → 合成」。

def _load_tech_capex_evidence(date):
    """Load an optional dated deep-research evidence artifact; never impute it."""
    path = os.path.join(OUT_DATA, f"tech_capex_{date}.json")
    if not os.path.exists(path):
        DATA_AUDIT["omitted_factors"].append("L1.US.tech_capex")
        return None, {"source": None, "quarter": None, "is_real": False,
                      "note": "exact dated evidence missing; factor omitted and weights renormalized"}
    try:
        with open(path) as f:
            d = json.load(f)
        _validate_loaded_payload("tech capex evidence", d, date, path)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"  ❌ invalid tech capex evidence: {e}") from e
    capex = d.get("capex_pct_ocf")
    if capex is None:
        raise SystemExit(f"  ❌ tech capex evidence missing capex_pct_ocf: {path}")
    if not d.get("publication_date") or not d.get("source"):
        raise SystemExit(f"  ❌ tech capex evidence requires publication_date and source: {path}")
    _record_source("tech capex evidence", path, "exact")
    return float(capex), {"source": d.get("source"),
                          "quarter": d.get("quarter"), "is_real": True,
                          "note": f"dated evidence {float(capex):.0%} (CapEx/OCF)"}

_tech_capex_value, _tech_capex_meta = _load_tech_capex_evidence(DATE)

# US factors (5 × 0.20)
us_employment = score_us_employment(VALUE_MAP["nfp_actual"], VALUE_MAP["unemployment"])
us_inflation  = score_us_inflation(VALUE_MAP["core_cpi_yoy"], VALUE_MAP["cpi_us_yoy"], VALUE_MAP["ppi_us_yoy"], VALUE_MAP["brent"])
us_monetary   = score_us_monetary(VALUE_MAP["fed_rate"])
us_financial  = score_us_financial(VALUE_MAP["vix"], VALUE_MAP["ust_30y"])
us_tech_capex = score_us_tech_capex(_tech_capex_value)

_us_factor_scores = {
    "employment": us_employment, "inflation": us_inflation, "monetary": us_monetary,
    "financial": us_financial, "tech_capex": us_tech_capex,
}
us_composite = _us_composite_fn(_us_factor_scores)
us_coverage = factor_coverage(_us_factor_scores, US_WEIGHTS)

# CN factors (manufacturing 0.25, consumption 0.20, credit 0.20, real_estate 0.15, policy 0.20)
cn_manufacturing = score_cn_manufacturing(macro.get("pmi_mfg"), macro.get("pmi_non_mfg"))
cn_consumption   = score_cn_consumption(macro.get("retail_sales_yoy"), macro.get("pmi_non_mfg"))
cn_credit        = score_cn_credit(macro.get("m2_yoy"), macro.get("m1_yoy"), macro.get("tsf_monthly"))
cn_real_estate   = None
cn_policy        = None
DATA_AUDIT["omitted_factors"].extend(["L1.CN.real_estate", "L1.CN.policy"])

_cn_factor_scores = {
    "manufacturing": cn_manufacturing, "consumption": cn_consumption, "credit": cn_credit,
    "real_estate": cn_real_estate, "policy": cn_policy,
}
cn_composite = _cn_composite_fn(_cn_factor_scores)
cn_coverage = factor_coverage(_cn_factor_scores, CN_WEIGHTS)

l1_composite = _l1_composite_fn(us_composite, cn_composite)

# ── Cross-validation ───────────────────────────────────────
# Brent $102 (< 105), no forced re-eval
# Check triple-rise: oil↑ + 10Y↓ + gold? 10Y 4.98% (↓from 5.07%), not applicable

# ── L2 Scoring ─────────────────────────────────────────────
# Disabled until an official, reproducible factor-source contract exists.
# Momentum remains in P1 as a diagnostic, but it is not promoted to a partial
# L2 score and cannot silently stand in for FF5 or a China style-factor model.
l2_composite = None
l2_status = {
    "status": "disabled",
    "reason": "FF5 and China style-factor models have no official reproducible source contract",
    "replacement_used": False,
}

# ── L3 Scoring ─────────────────────────────────────────────

# Sentiment factors
vix_val = VALUE_MAP["vix"]
if vix_val < 12: vix_score = 5
elif vix_val < 17: vix_score = 4
elif vix_val < 22: vix_score = 3
elif vix_val < 28: vix_score = 2
else: vix_score = 1

put_call = VALUE_MAP.get("put_call_ratio")
if put_call is None:
    pc_score = None
    DATA_AUDIT["omitted_factors"].append("L3.sentiment.put_call")
elif put_call > 1.0: pc_score = 1
elif put_call >= 0.5: pc_score = 2
else: pc_score = 3

aaii_spread = VALUE_MAP["aaii_spread"]
if aaii_spread is None:
    aaii_score = None
elif aaii_spread < -20: aaii_score = 1
elif aaii_spread < -10: aaii_score = 2
elif aaii_spread < 10: aaii_score = 3
elif aaii_spread < 20: aaii_score = 4
else: aaii_score = 5

dxy_score = None
DATA_AUDIT["omitted_factors"].append("L3.sentiment.dxy")

# Credit spread
credit_change = VALUE_MAP.get("credit_spread_change_20d")
if credit_change is None:
    cs_score = None
    DATA_AUDIT["omitted_factors"].append("L3.sentiment.credit_spread")
elif credit_change <= -0.10:
    cs_score = 4
elif credit_change >= 0.10:
    cs_score = 2
else:
    cs_score = 3

# AAII is kept as an observed field only. It was removed from L3 scoring on 2026-06-12.
sentiment_scores = {
    "vix": vix_score,
    "put_call": pc_score,
    "dxy": dxy_score,
    "credit_spread": cs_score,
}
sentiment_weights = L3_WEIGHTS["sentiment_factors"]
sentiment_weights = {key: value for key, value in sentiment_weights.items() if not key.startswith("_")}
sentiment_composite = round(
    sum(score * sentiment_weights[name] for name, score in sentiment_scores.items() if score is not None)
    / sum(sentiment_weights[name] for name, score in sentiment_scores.items() if score is not None),
    2,
)
sentiment_coverage = factor_coverage(sentiment_scores, sentiment_weights)
if sentiment_coverage["coverage_ratio"] < 0.50:
    raise SystemExit(
        f"  ❌ L3 sentiment coverage {sentiment_coverage['coverage_ratio']:.0%} is below 50%; "
        "provide at least two exact dated sentiment factors"
    )

# Risk from p1_upgrade l3_risk_scores
l3_risk = p1["l3_risk_scores"]
risk_scores = []
for _t in scoring_assets():
    _s = l3_risk.get(_t, {}).get("avg_risk_score")
    if _s is None:
        raise SystemExit(f"  ❌ P1 risk score missing for {_t}")
    risk_scores.append(_s)
risk_composite = round(sum(risk_scores) / len(risk_scores), 2)

l3_composite = round(
    sentiment_composite * L3_WEIGHTS["sentiment"] + risk_composite * L3_WEIGHTS["risk"],
    2,
)

# ── Composite ──────────────────────────────────────────────
composite = round(
    l1_composite * COMPOSITE_WEIGHTS["L1"]
    + l3_composite * COMPOSITE_WEIGHTS["L3"],
    2,
)

# ── Position map lookup ────────────────────────────────────
if composite < 1.5: band = "极端防御"; central = 10; pmin, pmax = 0, 10
elif composite < 2.0: band = "防御"; central = 20; pmin, pmax = 10, 20
elif composite < 2.5: band = "偏防御"; central = 35; pmin, pmax = 20, 35
elif composite < 3.0: band = "中性偏保守"; central = 45; pmin, pmax = 35, 50
elif composite < 3.5: band = "中性偏进攻"; central = 55; pmin, pmax = 50, 60
elif composite < 4.0: band = "进攻"; central = 70; pmin, pmax = 60, 70
else: band = "积极进攻"; central = 85; pmin, pmax = 70, 85

# ── Check trigger: 30Y > 5.0% ──────────────────────────────
trigger_1_active = False
if VALUE_MAP["ust_30y"] >= 5.0:
    trigger_1_active = True

alerts = []
if trigger_1_active:
    alerts.append({
        "id": "ust_30y_above_5pct",
        "level": "red",
        "value": VALUE_MAP["ust_30y"],
        "threshold": ">=5.0%",
        "raised": DATE,
        "action": "Long-end yield is above the configured risk threshold; reassess growth-duration and gold exposure.",
        "source": "raw_macro_us dated FRED observation",
    })

# ── Output ─────────────────────────────────────────────────

DATA_AUDIT["omitted_factors"] = sorted(set(DATA_AUDIT["omitted_factors"]))
DATA_AUDIT["static_fallback_fields"] = sorted(set(DATA_AUDIT["static_fallback_fields"]))
DATA_AUDIT["quality_notes"].extend(
    [
        f"L1_US observed-weight coverage={us_coverage['coverage_ratio']:.4f}",
        f"L1_CN observed-weight coverage={cn_coverage['coverage_ratio']:.4f}",
        "L2 disabled: no official reproducible FF5/China style-factor source contract",
        f"L3 sentiment observed-weight coverage={sentiment_coverage['coverage_ratio']:.4f}",
    ]
)

def write_json(filename, data):
    path = os.path.join(OUT_SCORES, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"  ✅ {path}")

print(f"\n{'='*60}")
print(f"  ⭐ 每日评分 {DATE}")
print(f"{'='*60}")

# L1
l1_report = {
    "timestamp": utc_now_iso(),
    "as_of_date": DATE,
    "data_audit": DATA_AUDIT,
    "L1_US_composite": us_composite,
    "L1_CN_composite": cn_composite,
    "L1_composite": l1_composite,
    "coverage": {"us": us_coverage, "cn": cn_coverage},
    "us": {
        "employment": {"score": us_employment, "value": {"nfp_actual_k": VALUE_MAP['nfp_actual'], "unemployment": VALUE_MAP['unemployment']}, "rationale": f"NFP {VALUE_MAP['nfp_actual']}K (FRED PAYEMS), unemployment {VALUE_MAP['unemployment']}% — 数据驱动(阈值见 score_us_employment)"},
        "inflation": {"score": us_inflation, "value": {"core_cpi_yoy": f"{VALUE_MAP['core_cpi_yoy']}%", "cpi_yoy": f"{VALUE_MAP['cpi_us_yoy']}%", "ppi_yoy": f"{VALUE_MAP['ppi_us_yoy']}%", "brent": f"${VALUE_MAP['brent']}"}, "rationale": f"Core CPI {VALUE_MAP['core_cpi_yoy']}%, headline CPI {VALUE_MAP['cpi_us_yoy']}%, PPI {VALUE_MAP['ppi_us_yoy']}%, Brent ${VALUE_MAP['brent']}"},
        "monetary": {"score": us_monetary, "value": {"fed_rate": f"{VALUE_MAP['fed_rate']}%"}, "rationale": f"Fed funds {VALUE_MAP['fed_rate']}% (FRED) — 数据驱动(利率阈值)"},
        "financial": {"score": us_financial, "value": {"vix": VALUE_MAP['vix'], "spx": VALUE_MAP['spx'], "ust_30y": VALUE_MAP['ust_30y']}, "rationale": f"VIX {VALUE_MAP['vix']}, SPX {VALUE_MAP['spx']}, 30Y {VALUE_MAP['ust_30y']}%"},
        "tech_capex": {"score": us_tech_capex, "value": _tech_capex_value,
                       "rationale": f"tech_capex({_tech_capex_meta['quarter'] or 'N/A'}): {_tech_capex_meta['note']} | 来源: {_tech_capex_meta['source']}"}
    },
    "cn": {
        "manufacturing": {"score": cn_manufacturing, "value": {"pmi_mfg": macro.get("pmi_mfg"), "pmi_non_mfg": macro.get("pmi_non_mfg")}, "rationale": f"PMI mfg {macro.get('pmi_mfg')} (barely expansion), non-mfg {macro.get('pmi_non_mfg')}"},
        "consumption": {"score": cn_consumption, "value": {"retail_yoy": f"{macro.get('retail_sales_yoy')}%", "pmi_non_mfg": macro.get("pmi_non_mfg")}, "rationale": f"Retail {macro.get('retail_sales_yoy')}% (恶化), non-mfg {macro.get('pmi_non_mfg')}"},
        "credit": {"score": cn_credit, "value": {"m2": f"{macro.get('m2_yoy')}%", "m1": f"{macro.get('m1_yoy')}%", "tsf": macro.get('tsf_monthly')}, "rationale": "Credit score uses M2/M1; TSF is explicitly unavailable when null."},
        "real_estate": {"score": cn_real_estate, "value": None, "rationale": "not scored: no exact dated input"},
        "policy": {"score": cn_policy, "value": {"lpr_1y": macro.get('lpr_1y'), "lpr_5y": macro.get('lpr_5y')}, "rationale": "observed LPR only; qualitative policy factor omitted"}
    },
    "assessment": f"US {us_composite} (coverage {us_coverage['coverage_ratio']:.0%}) | CN {cn_composite} (coverage {cn_coverage['coverage_ratio']:.0%}) → L1 {l1_composite}. NFP={VALUE_MAP['nfp_actual']}K, core CPI={VALUE_MAP['core_cpi_yoy']}%, 30Y={VALUE_MAP['ust_30y']}%, CN retail={macro.get('retail_sales_yoy')}%. Missing factors were omitted without neutral imputation."
}
write_json(f"scores_L1_{DATE}.json", l1_report)

# L2
l2_report = {
    "timestamp": utc_now_iso(),
    "as_of_date": DATE,
    "methodology_version": SCORING_METHODOLOGY,
    "data_audit": DATA_AUDIT,
    "L2_composite": l2_composite,
    **l2_status,
    "composite_weight": COMPOSITE_WEIGHTS["L2"],
    "assessment": "L2 is disabled and contributes no score. P1 momentum/risk diagnostics are not an FF5 replacement."
}
write_json(f"scores_L2_{DATE}.json", l2_report)

# L3
l3_report = {
    "timestamp": utc_now_iso(),
    "as_of_date": DATE,
    "data_audit": DATA_AUDIT,
    "L3_composite": l3_composite,
    "coverage": {"sentiment": sentiment_coverage, "risk_assets": len(risk_scores)},
    "sentiment": {
        "composite": sentiment_composite,
        "vix": {"score": vix_score, "value": vix_val, "interpretation": "risk-appetite score: high VIX maps to low score"},
        "put_call": {"score": pc_score, "value": put_call, "note": "not scored when exact source is absent"},
        "aaii": {"score": None, "value": aaii_spread, "interpretation": "observed only; removed from L3 scoring on 2026-06-12"},
        "dxy": {"score": dxy_score, "value": VALUE_MAP.get("dxy"), "note": "not scored without a dated trend input"},
        "credit_spread": {"score": cs_score, "value": VALUE_MAP.get("credit_spread"), "change_20d": credit_change}
    },
    "rsi_14": rsi_values,
    "risk": {
        "composite": risk_composite,
        "per_etf": l3_risk
    },
    "assessment": f"Sentiment {sentiment_composite} (coverage {sentiment_coverage['coverage_ratio']:.0%}) | Risk {risk_composite}. RSI(14): {rsi_values}. Missing sentiment factors were omitted without neutral imputation."
}
write_json(f"scores_L3_{DATE}.json", l3_report)

# Composite
def _load_previous_composite(date):
    candidates = []
    for path in glob.glob(os.path.join(OUT_SCORES, "scores_composite_*.json")):
        previous_date = _date_from_filename(path, "scores_composite")
        if previous_date and previous_date < date:
            candidates.append((previous_date, path))
    if not candidates:
        return None, None
    for previous_date, previous_path in reversed(sorted(candidates)):
        try:
            with open(previous_path, encoding="utf-8") as file:
                payload = json.load(file)
            if payload.get("methodology_version") == SCORING_METHODOLOGY:
                return payload.get("composite"), previous_date
        except (OSError, json.JSONDecodeError):
            continue
    return None, None


previous_composite, previous_score_date = _load_previous_composite(DATE)
score_change = round(composite - previous_composite, 2) if isinstance(previous_composite, (int, float)) else None
comp_report = {
    "timestamp": utc_now_iso(),
    "as_of_date": DATE,
    "methodology_version": SCORING_METHODOLOGY,
    "data_audit": DATA_AUDIT,
    "composite": composite,
    "L1": l1_composite,
    "L2": l2_composite,
    "L3": l3_composite,
    "formula": f"{l1_composite}×{COMPOSITE_WEIGHTS['L1']:.4f} + {l3_composite}×{COMPOSITE_WEIGHTS['L3']:.4f} = {composite} (L2 disabled)",
    "position_band": band,
    "position_range": f"{pmin}-{pmax}%",
    "position_central": central,
    "previous_composite": previous_composite,
    "previous_score_date": previous_score_date,
    "change": score_change
}
write_json(f"scores_composite_{DATE}.json", comp_report)

# ── Update system_state.json ───────────────────────────────
if os.path.exists(OUT_STATE):
    with open(OUT_STATE) as f:
        state = json.load(f)
else:
    state = {"sop_versions": {}}

state_date = state.get("scores", {}).get("as_of_date")
should_update_state = not isinstance(state_date, str) or state_date <= DATE
if should_update_state:
    generated_at = utc_now_iso()
    state["timestamp"] = generated_at
    state["scores"] = {
        "as_of_date": DATE,
        "composite": composite,
        "L1": l1_composite,
        "L1_us": us_composite,
        "L1_cn": cn_composite,
        "L2": l2_composite,
        "L3": l3_composite,
        "last_updated": generated_at,
        "methodology_version": SCORING_METHODOLOGY,
    }
    state["data_audit"] = DATA_AUDIT
    position = state.setdefault("position", {})
    position["band_label"] = band
    position["central_pct"] = central
    position["range"] = f"{pmin}-{pmax}%"
    current_actual = position.get("current_actual_pct")
    position["deviation"] = (
        f"{current_actual-central:+}pp ({band})"
        if isinstance(current_actual, (int, float))
        else f"current_actual_pct unavailable ({band})"
    )
    state["alerts"] = {"active_count": len(alerts), "active_list": alerts}
    state["narrative_context"] = (
        f"Score {composite} ({band}), previous dated score {previous_composite}. "
        f"L1-US {us_composite}/L1-CN {cn_composite} | L2 disabled | L3 {l3_composite}. "
        f"VIX {VALUE_MAP['vix']} | 30Y {VALUE_MAP['ust_30y']}% | Brent {VALUE_MAP['brent']} | "
        f"CN retail {macro.get('retail_sales_yoy', 'N/A')}%."
    )
    os.makedirs(os.path.dirname(OUT_STATE), exist_ok=True)
    with open(OUT_STATE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  ✅ system_state.json updated to {DATE} (previous dated score: {previous_composite}, Δ={score_change})")
else:
    print(f"\n  ℹ️ historical score {DATE} did not overwrite newer system_state {state_date}")

# ── Alert check ────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  ⚠️  Alert Check")
print(f"{'='*60}")
if not trigger_1_active:
    print(f"  ✅ Trigger 1 (30Y>5%): {VALUE_MAP['ust_30y']}% ≤ 5.0% → ALERT CLEARED")

active_count = len(alerts)
print(f"  Active alerts: {active_count}")
if active_count == 0:
    print(f"  ✅ All alerts clear")

print(f"\n{'='*60}")
print(f"  📊 摘要: L1={l1_composite} L2={l2_composite} L3={l3_composite}")
print(f"  ⭐ COMPOSITE: {composite} → {band} ({pmin}-{pmax}%, central {central}%)")
print(f"  📈 Change from previous dated score: {score_change}")
print(f"{'='*60}\n")
