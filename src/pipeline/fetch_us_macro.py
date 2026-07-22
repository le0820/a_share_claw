#!/usr/bin/env python3
"""fetch_us_macro.py — 拉取美宏观数据（FRED 官方源，无需 API key）

数据源：FRED (Federal Reserve Economic Data) 公开 CSV 接口
  https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>

设计原则（2026-07-08 起）：
  - 只填有干净官方来源的字段；缺失即留空，绝不填默认值或代理值。
  - 以下字段 **不再获取、也不再填默认值**（无干净免 key 来源）：
      put_call_ratio   —— CBOE put/call 无免 key 实时源
      nfp_consensus    —— 卖方共识无免 key 实时源
      dxy              —— 真实 ICE DXY 仍无干净免 key 来源；FRED DTWEXBGS
                          以独立的 broad_dollar 字段保存，绝不改名为 dxy。
  - aaii_bull/aaii_bear 同理暂不获取（非必需字段）。

输出：data/raw/raw_macro_us_{DATE}.json
每个字段附带 _provenance（来源序列 + 观察日期），供 data_audit 透明追溯。
"""

import argparse
import json
import os
from datetime import datetime

import requests

from pipeline_contracts import utc_now_iso, validate_payload
from pipeline_paths import RAW_DIR, current_date, validate_as_of_date

DATA_DIR = str(RAW_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"

# field -> (FRED series id, transform)
#   transform: None=最新观测值; "yoy"=同比; "mom"=环比; "nfp_net"=非农月度净增(千)
SERIES = {
    "vix":           ("VIXCLS", None),
    "ust_2y":        ("DGS2", None),
    "ust_10y":       ("DGS10", None),
    "ust_30y":       ("DGS30", None),
    "tips_5y":       ("DFII5", None),
    "tips_10y":      ("DFII10", None),
    "breakeven_5y":  ("T5YIE", None),
    "breakeven_10y": ("T10YIE", None),
    "broad_dollar":  ("DTWEXBGS", None),
    "nfci":          ("NFCI", None),
    "brent":         ("DCOILBRENTEU", None),
    "spx":           ("SP500", None),
    "cpi_us_yoy":    ("CPIAUCSL", "yoy"),
    "core_cpi_yoy":  ("CPILFESL", "yoy"),
    "cpi_us_mom":    ("CPIAUCSL", "mom"),
    "ppi_us_yoy":    ("PPIFIS", "yoy"),
    "ppi_us_mom":    ("PPIFIS", "mom"),
    "pce_us_yoy":    ("PCEPI", "yoy"),
    "pce_us_mom":    ("PCEPI", "mom"),
    "core_pce_us_yoy": ("PCEPILFE", "yoy"),
    "core_pce_us_mom": ("PCEPILFE", "mom"),
    "fed_rate":      ("FEDFUNDS", None),
    "nfp_actual":    ("PAYEMS", "nfp_net"),
    "unemployment":  ("UNRATE", None),
    "jp_jgb_10y":    ("IRLTLT01JPM156N", None),
    "usdjpy":        ("DEXJPUS", None),
    "credit_spread": ("BAA10Y", None),
}

# 明确不获取的字段（无干净免 key 来源；宁可缺失也不要脏数据）
DROPPED_FIELDS = ["put_call_ratio", "nfp_consensus", "dxy", "aaii_bull", "aaii_bear"]


def _fetch_csv(series_id: str):
    """Fetch the current-vintage FRED graph CSV for a current-date run."""
    url = FRED_URL.format(id=series_id)
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text if response.text.strip() else None
    except requests.RequestException:
        return None


def _parse_rows(text: str):
    """返回 [(date_str, value), ...] 升序，跳过注释/表头/空值。"""
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("date"):
            continue
        parts = line.split(",")
        if len(parts) < 2:
            continue
        d, v = parts[0].strip(), parts[1].strip()
        if v in ("", ".", "NA"):
            continue
        try:
            rows.append((d, float(v)))
        except ValueError:
            continue
    return rows


def _latest_le(rows, as_of):
    """最后一个 date <= as_of 的观测。"""
    best = None
    for d, val in rows:
        if d <= as_of:
            best = (d, val)
        else:
            break
    return best


def _yoy(rows, as_of):
    cur = _latest_le(rows, as_of)
    if not cur:
        return None, None
    ym = cur[0][:7]
    y, m = int(ym[:4]), int(ym[5:7])
    m -= 12
    if m <= 0:
        m += 12
        y -= 1
    prev_ym = f"{y:04d}-{m:02d}"
    prev = None
    for d, val in rows:
        if d.startswith(prev_ym):
            prev = val
            break
    if prev in (None, 0):
        return None, cur[0]
    return round((cur[1] / prev - 1) * 100, 2), cur[0]


def _nfp_net(rows, as_of):
    cur = _latest_le(rows, as_of)
    if not cur:
        return None, None
    ym = cur[0][:7]
    y, m = int(ym[:4]), int(ym[5:7])
    m -= 1
    if m <= 0:
        m += 12
        y -= 1
    prev_ym = f"{y:04d}-{m:02d}"
    prev = None
    for d, val in rows:
        if d.startswith(prev_ym):
            prev = val
            break
    if prev is None:
        return None, cur[0]
    return round(cur[1] - prev, 1), cur[0]


def _mom(rows, as_of):
    cur = _latest_le(rows, as_of)
    if not cur:
        return None, None
    current_date = datetime.strptime(cur[0][:7], "%Y-%m")
    if current_date.month == 1:
        previous_month = f"{current_date.year - 1:04d}-12"
    else:
        previous_month = f"{current_date.year:04d}-{current_date.month - 1:02d}"
    previous = next((value for date, value in rows if date.startswith(previous_month)), None)
    if previous in (None, 0):
        return None, cur[0]
    return round((cur[1] / previous - 1) * 100, 3), cur[0]


def _change_over_observations(rows, as_of, periods=20):
    eligible = [(date, value) for date, value in rows if date <= as_of]
    if len(eligible) <= periods:
        return None
    return round(eligible[-1][1] - eligible[-periods - 1][1], 4)


def main():
    parser = argparse.ArgumentParser(description="Fetch US macro data from FRED for an explicit as-of date.")
    parser.add_argument("legacy_date", nargs="?", help="Backward-compatible YYYYMMDD date.")
    parser.add_argument("--date", dest="date", help="As-of date, YYYYMMDD.")
    args = parser.parse_args()
    date_str = args.date or args.legacy_date or current_date().strftime("%Y%m%d")
    try:
        validate_as_of_date(date_str)
    except ValueError as exc:
        parser.error(str(exc))
    as_of = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    out_file = os.path.join(DATA_DIR, f"raw_macro_us_{date_str}.json")
    today = current_date().strftime("%Y%m%d")
    if date_str < today:
        if not os.path.exists(out_file):
            raise SystemExit(
                f"[fetch_us_macro] historical current-vintage fetch is forbidden for {date_str}; "
                f"restore an archived FRED/ALFRED artifact at {out_file}"
            )
        try:
            with open(out_file, encoding="utf-8") as existing_file:
                existing = json.load(existing_file)
            validate_payload(existing, date_str, label="US macro archive", path=out_file)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"[fetch_us_macro] invalid historical archive: {exc}") from exc
        print(f"[fetch_us_macro] historical exact artifact reused -> {out_file}")
        return

    generated_at = utc_now_iso()

    result = {
        "timestamp": generated_at,
        "as_of_date": date_str,
        "source": "FRED current-vintage graph CSV (current-date runs only)",
        "_meta": {
            "as_of_date": date_str,
            "generated_at": generated_at,
            "source": "FRED current-vintage graph CSV",
            "source_timestamp": generated_at,
            "data_period": f"latest observation <= {as_of}",
            "fallback_status": "none",
        },
        "_provenance": {},
        "_dropped_fields": DROPPED_FIELDS,
        "_errors": [],
    }

    for field, (fid, transform) in SERIES.items():
        text = _fetch_csv(fid)
        rows = _parse_rows(text)
        if not rows:
            result["_errors"].append(f"{field}: FRED {fid} fetch failed/empty")
            continue

        if transform is None:
            lv = _latest_le(rows, as_of)
            if lv:
                result[field] = round(lv[1], 4)
                result["_provenance"][field] = {"series": fid, "observation_date": lv[0]}
                if field == "credit_spread":
                    result["credit_spread_change_20d"] = _change_over_observations(rows, as_of)
        elif transform == "yoy":
            val, obs = _yoy(rows, as_of)
            if val is not None:
                result[field] = val
                result["_provenance"][field] = {"series": fid, "observation_date": obs}
        elif transform == "nfp_net":
            val, obs = _nfp_net(rows, as_of)
            if val is not None:
                result[field] = val
                result["_provenance"][field] = {"series": fid, "observation_date": obs}
        elif transform == "mom":
            val, obs = _mom(rows, as_of)
            if val is not None:
                result[field] = val
                result["_provenance"][field] = {"series": fid, "observation_date": obs}

        if field not in result:
            result["_errors"].append(f"{field}: no observation available by {as_of}")

    if result["_errors"]:
        result["_meta"]["fallback_status"] = "unverified"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)

    n_ok = sum(1 for k in SERIES if k in result)
    print(f"[fetch_us_macro] FRED 拉取完成: {n_ok}/{len(SERIES)} 字段 → {out_file}")
    if result["_errors"]:
        for e in result["_errors"]:
            print(f"  ⚠️ {e}")
    print(f"  未获取字段(无干净来源): {', '.join(DROPPED_FIELDS)}")
    if result.get("ust_30y"):
        flag = "🔴 >5% 红色警报" if result["ust_30y"] >= 5.0 else "✅ <5%"
        print(
            f"  30Y={result['ust_30y']}% {flag} | VIX={result.get('vix')} | "
            f"broad-dollar={result.get('broad_dollar')} | exact DXY=未获取"
        )
    if result["_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
