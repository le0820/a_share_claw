#!/usr/bin/env python3
"""rules_L1.py — L1 宏观评分规则（数据驱动, 阈值版 v1, 2026-07-09）

设计原则
--------
1. 纯函数：输入真实数值（FRED / akshare / 手动注入），输出 1-5 整数分。无 I/O、无写死分数。
2. 阈值逻辑集中在此文件；权重只从 ``src/compiled/weight_matrix.json`` 加载。
3. 缺失来源 → 返回 None；合成时按已观测因子重归一，不注入中性分。

tech_capex 因子
--------------
由云计算巨头（MSFT/AMZN/GOOG/META 等）财报的 CapEx/OCF 加权体现。
由 Agent host 取证后写入同日 ``data/raw/tech_capex_<DATE>.json``；缺失时不评分。
映射逻辑：CapEx/OCF 越高 = 资本开支越激进 = 现金流压力/过度投资信号 → 越低分。
  >=0.80 → 2（激进透支）  >=0.65 → 3  >=0.50 → 4  <0.50 → 5（健康）
  （与历史注释「~70% → 2-3」一致）
"""

import json
from pathlib import Path
from typing import Optional


_WEIGHT_PATH = Path(__file__).resolve().parents[1] / "compiled" / "weight_matrix.json"
_WEIGHT_MATRIX = json.loads(_WEIGHT_PATH.read_text(encoding="utf-8"))
_L1_WEIGHTS = _WEIGHT_MATRIX["L1_weights"]
_US_WEIGHT_ALIASES = {
    "employment": "employment_growth",
    "inflation": "inflation_wages",
    "monetary": "monetary_policy",
    "financial": "financial_conditions",
    "tech_capex": "tech_capex",
}
US_WEIGHTS = {
    executable: float(_L1_WEIGHTS["us_factor_weights"][compiled])
    for executable, compiled in _US_WEIGHT_ALIASES.items()
}
CN_WEIGHTS = dict(_L1_WEIGHTS["cn_factor_weights"])
L1_US_WEIGHT = float(_L1_WEIGHTS["us_cn"]["us"])
L1_CN_WEIGHT = float(_L1_WEIGHTS["us_cn"]["cn"])


# ── US factors ─────────────────────────────────────────────
def score_us_employment(nfp_k: Optional[float], unrate: Optional[float]) -> Optional[int]:
    """NFP 月度净增(千人) + 失业率。共识字段已弃用(无干净源)。"""
    if nfp_k is None:
        return None
    s = 3
    if nfp_k >= 200: s = 5
    elif nfp_k >= 100: s = 4
    elif nfp_k >= 50: s = 3
    elif nfp_k >= 0: s = 2
    else: s = 1
    if unrate is not None and unrate > 4.5:
        s = max(1, s - 1)
    return s


def score_us_inflation(core_cpi: Optional[float], cpi: Optional[float],
                       ppi: Optional[float], brent: Optional[float]) -> Optional[int]:
    """核心CPI为主, PPI飙升加粘滞惩罚。brent 暂未参与阈值(留扩展位)。"""
    if core_cpi is None:
        return None
    if core_cpi > 3.5: s = 1
    elif core_cpi > 3.0: s = 2
    elif core_cpi > 2.5: s = 3
    elif core_cpi > 2.0: s = 4
    else: s = 5
    if ppi is not None and ppi > 10:
        s = max(1, s - 1)  # PPI>10% 成本推动粘滞
    return s


def score_us_monetary(fed_rate: Optional[float]) -> Optional[int]:
    """利率水平: 越高越限制→越低分。"""
    if fed_rate is None:
        return None
    if fed_rate >= 5.0: return 1
    if fed_rate >= 4.0: return 2
    if fed_rate >= 3.0: return 3
    if fed_rate >= 2.0: return 4
    return 5


def score_us_financial(vix: Optional[float], ust_30y: Optional[float]) -> Optional[int]:
    """VIX平静度 + 30Y破5%红警惩罚。"""
    if vix is None:
        return None
    if vix < 15: s = 5
    elif vix < 18: s = 4
    elif vix < 22: s = 3
    elif vix < 30: s = 2
    else: s = 1
    if ust_30y is not None and ust_30y >= 5.0:
        s = max(1, s - 1)
    return s


def score_us_tech_capex(capex_pct_ocf: Optional[float]) -> Optional[int]:
    """云计算巨头加权 CapEx/OCF。缺源时不评分。"""
    if capex_pct_ocf is None:
        return None
    if capex_pct_ocf >= 0.80: return 2
    if capex_pct_ocf >= 0.65: return 3
    if capex_pct_ocf >= 0.50: return 4
    return 5


# ── CN factors ─────────────────────────────────────────────
def score_cn_manufacturing(pmi_mfg: Optional[float], pmi_non_mfg: Optional[float]) -> Optional[int]:
    if pmi_mfg is None:
        return None
    if pmi_mfg >= 52: s = 5
    elif pmi_mfg >= 51: s = 4
    elif pmi_mfg >= 50: s = 3
    elif pmi_mfg >= 49: s = 2
    else: s = 1
    if pmi_non_mfg is not None and pmi_non_mfg < 50:
        s = max(1, s - 1)
    return s


def score_cn_consumption(retail_yoy: Optional[float], pmi_non_mfg: Optional[float]) -> Optional[int]:
    if retail_yoy is None:
        return None
    if retail_yoy >= 6: s = 5
    elif retail_yoy >= 4: s = 4
    elif retail_yoy >= 2: s = 3
    elif retail_yoy >= 0: s = 2
    else: s = 1
    if pmi_non_mfg is not None and pmi_non_mfg < 50:
        s = max(1, s - 1)
    return s


def score_cn_credit(m2: Optional[float], m1: Optional[float], tsf: Optional[float]) -> Optional[int]:
    if m2 is None:
        return None
    if m2 >= 10: s = 4
    elif m2 >= 8: s = 3
    elif m2 >= 6: s = 2
    else: s = 1
    if m1 is not None and m2 is not None and (m2 - m1) >= 4:
        s = max(1, s - 1)  # M1-M2剪刀差过大→传导不畅
    return s


# ── Composite helpers ──────────────────────────────────────
def weighted_composite(scores: dict, weights: dict) -> float:
    available = {key: value for key, value in scores.items() if key in weights and value is not None}
    denominator = sum(float(weights[key]) for key in available)
    if denominator <= 0:
        raise ValueError("cannot score a layer with zero observed factor weight")
    return round(sum(float(value) * float(weights[key]) for key, value in available.items()) / denominator, 2)


def factor_coverage(scores: dict, weights: dict) -> dict:
    total = sum(float(value) for value in weights.values())
    observed = sum(float(weights[key]) for key, value in scores.items() if key in weights and value is not None)
    omitted = [key for key in weights if scores.get(key) is None]
    return {
        "observed_weight": round(observed, 4),
        "total_weight": round(total, 4),
        "coverage_ratio": round(observed / total, 4) if total else 0.0,
        "omitted_factors": omitted,
        "method": "renormalize_observed_weights_without_neutral_imputation",
    }


def us_composite(scores: dict) -> float:
    return weighted_composite(scores, US_WEIGHTS)


def cn_composite(scores: dict) -> float:
    return weighted_composite(scores, CN_WEIGHTS)


def l1_composite(us: float, cn: float) -> float:
    return round(us * L1_US_WEIGHT + cn * L1_CN_WEIGHT, 2)
