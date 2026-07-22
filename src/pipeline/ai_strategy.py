"""Pure factor and position logic for the independent AI sector strategy."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return float(max(low, min(high, value)))


def _interpolate(value: float | None, knots: list[tuple[float, float]]) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    ordered = sorted(knots)
    if value <= ordered[0][0]:
        return ordered[0][1]
    if value >= ordered[-1][0]:
        return ordered[-1][1]
    for (left_x, left_y), (right_x, right_y) in zip(ordered, ordered[1:]):
        if left_x <= value <= right_x:
            fraction = (value - left_x) / (right_x - left_x)
            return left_y + fraction * (right_y - left_y)
    return None


def _weighted_observed(
    values: dict[str, float | None], weights: dict[str, float]
) -> tuple[float | None, float, list[str]]:
    observed = {name: value for name, value in values.items() if value is not None and name in weights}
    observed_weight = sum(weights[name] for name in observed)
    total_weight = sum(weights.values())
    if observed_weight <= 0 or total_weight <= 0:
        return None, 0.0, sorted(values)
    score = sum(float(value) * weights[name] for name, value in observed.items()) / observed_weight
    missing = sorted(name for name in weights if name not in observed)
    return float(score), observed_weight / total_weight, missing


def growth_state(raw: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    aggregate = raw.get("aggregate", {})
    capex_growth = aggregate.get("capex", {}).get("yoy")
    ocf_growth = aggregate.get("operating_cash_flow", {}).get("yoy")
    revenue_growth = aggregate.get("revenue", {}).get("yoy")
    earnings_growth = aggregate.get("operating_income", {}).get("yoy")
    capex_to_ocf = aggregate.get("funding", {}).get("capex_to_ocf")
    minimum_companies = int(rules["growth"].get("minimum_companies_per_core_metric", 1))
    core_company_counts = {
        "capex_impulse": aggregate.get("capex", {}).get("companies_observed", 0),
        "funding_sustainability": aggregate.get("funding", {}).get("companies_observed", 0),
        "monetization": aggregate.get("revenue", {}).get("companies_observed", 0),
    }

    capex_score = _interpolate(
        capex_growth,
        [(-0.20, 0.0), (0.0, 0.35), (0.10, 0.55), (0.25, 0.75), (0.50, 1.0)],
    )
    ocf_score = _interpolate(
        ocf_growth,
        [(-0.20, 0.0), (0.0, 0.50), (0.20, 0.75), (0.40, 1.0)],
    )
    funding_ratio_score = _interpolate(
        capex_to_ocf,
        [(0.0, 1.0), (0.80, 1.0), (1.0, 0.65), (1.25, 0.25), (1.50, 0.0)],
    )
    funding_score = (
        0.60 * ocf_score + 0.40 * funding_ratio_score
        if ocf_score is not None and funding_ratio_score is not None
        else None
    )
    monetization_score = _interpolate(
        revenue_growth,
        [(-0.10, 0.0), (0.0, 0.35), (0.10, 0.65), (0.20, 1.0)],
    )
    earnings_score = _interpolate(
        earnings_growth,
        [(-0.20, 0.0), (0.0, 0.40), (0.15, 0.70), (0.35, 1.0)],
    )
    components = {
        "capex_impulse": capex_score if core_company_counts["capex_impulse"] >= minimum_companies else None,
        "funding_sustainability": (
            funding_score if core_company_counts["funding_sustainability"] >= minimum_companies else None
        ),
        "monetization": monetization_score if core_company_counts["monetization"] >= minimum_companies else None,
        "gpu_price_availability": None,
        "token_economics": None,
        "enterprise_earnings": earnings_score,
    }
    score, coverage, missing = _weighted_observed(components, rules["growth"]["weights"])
    required = rules["growth"]["required_core"]
    missing_required = [name for name in required if components.get(name) is None]
    minimum_coverage = float(rules["growth"]["minimum_coverage"])
    if score is None or coverage < minimum_coverage or missing_required:
        regime = "unavailable"
    elif capex_growth is not None and revenue_growth is not None and capex_growth < 0 and revenue_growth < 0 and score < 0.40:
        regime = "contraction"
    elif capex_growth is not None and revenue_growth is not None and capex_growth > 0 and revenue_growth >= 0 and score >= 0.60:
        regime = "expansion"
    else:
        regime = "plateau"
    requested_companies = max(raw.get("data_audit", {}).get("requested_company_count", 1), 1)
    company_coverage = min(core_company_counts.values()) / requested_companies
    confidence = _clip(coverage * company_coverage)
    return {
        "score": round(score, 6) if score is not None else None,
        "regime": regime,
        "coverage": round(coverage, 6),
        "confidence": round(confidence, 6),
        "components": {name: round(value, 6) if value is not None else None for name, value in components.items()},
        "raw_metrics": {
            "capex_yoy": capex_growth,
            "operating_cash_flow_yoy": ocf_growth,
            "revenue_yoy": revenue_growth,
            "operating_income_yoy": earnings_growth,
            "capex_to_ocf": capex_to_ocf,
            "capex_growth_minus_ocf_growth": aggregate.get("funding", {}).get(
                "capex_growth_minus_ocf_growth"
            ),
        },
        "missing_components": missing,
        "missing_required": missing_required,
        "core_company_counts": core_company_counts,
        "notes": [
            "GPU price is not scored without an availability/utilization companion series.",
            "Token list price is not scored without a fixed basket and usage-volume companion series.",
            "SEC revenue and operating income are hyperscaler-wide proxies, not pure AI segment disclosures.",
        ],
    }


def _rolling_slope_quality(prices: pd.Series, window: int) -> pd.Series:
    output = pd.Series(index=prices.index, dtype=float)
    x = np.arange(window, dtype=float)
    for index in range(window - 1, len(prices)):
        sample = prices.iloc[index - window + 1 : index + 1]
        if sample.isna().any() or (sample <= 0).any():
            continue
        y = np.log(sample.to_numpy(dtype=float))
        slope, intercept = np.polyfit(x, y, 1)
        fitted = intercept + slope * x
        total = float(np.sum((y - y.mean()) ** 2))
        residual = float(np.sum((y - fitted) ** 2))
        r_squared = 1.0 - residual / total if total > 0 else 0.0
        output.iloc[index] = slope * 252 * max(r_squared, 0.0)
    return output


def _percentile(series: pd.Series, value: float | None = None, minimum: int = 40) -> float | None:
    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < minimum:
        return None
    current = float(clean.iloc[-1] if value is None else value)
    history = clean.iloc[-756:]
    return float((history <= current).mean())


def _standardized_turn(series: pd.Series) -> pd.Series:
    rolling_scale = series.rolling(252, min_periods=40).std(ddof=1).replace(0, np.nan)
    return np.tanh(series / rolling_scale)


def momentum_state(sector: pd.Series, benchmark: pd.Series, rules: dict[str, Any]) -> dict[str, Any]:
    aligned = pd.concat({"sector": sector, "benchmark": benchmark}, axis=1, join="inner").dropna()
    if len(aligned) < 300:
        raise ValueError(f"AI momentum requires at least 300 aligned closes; got {len(aligned)}")
    metrics = {
        "return_12m_ex_latest_month": aligned["sector"].shift(21) / aligned["sector"].shift(252) - 1,
        "relative_strength_3m": aligned["sector"].pct_change(63) - aligned["benchmark"].pct_change(63),
        "slope_20d_quality": _rolling_slope_quality(aligned["sector"], 20),
        "slope_60d_quality": _rolling_slope_quality(aligned["sector"], 60),
    }
    percentiles = {name: _percentile(series) for name, series in metrics.items()}
    level, coverage, missing = _weighted_observed(percentiles, rules["tactical"]["momentum_weights"])

    relative_strength_20d = aligned["sector"].pct_change(20) - aligned["benchmark"].pct_change(20)
    slope_acceleration = metrics["slope_20d_quality"].diff(5)
    rs_acceleration = relative_strength_20d.diff(5)
    turn_series = pd.concat(
        {
            "slope": _standardized_turn(slope_acceleration),
            "relative_strength": _standardized_turn(rs_acceleration),
        },
        axis=1,
    ).mean(axis=1).dropna()
    if turn_series.empty:
        turn = None
        positive_streak = 0
    else:
        turn = float(turn_series.iloc[-1])
        positive_streak = 0
        for value in reversed(turn_series.to_list()):
            if value > 0:
                positive_streak += 1
            else:
                break
    return {
        "effective_trade_date": aligned.index[-1].strftime("%Y-%m-%d"),
        "level_percentile": round(level, 6) if level is not None else None,
        "turn": round(turn, 6) if turn is not None else None,
        "turn_positive_streak": positive_streak,
        "coverage": round(coverage, 6),
        "components": {
            name: {
                "value": round(float(series.dropna().iloc[-1]), 6) if not series.dropna().empty else None,
                "percentile": round(percentiles[name], 6) if percentiles[name] is not None else None,
            }
            for name, series in metrics.items()
        },
        "missing_components": missing,
    }


def history_series(history: dict[str, Any]) -> pd.Series:
    observations = history.get("observations", [])
    series = pd.Series(
        [row.get("value") for row in observations],
        index=pd.to_datetime([row.get("observation_date") for row in observations]),
        dtype=float,
    )
    return series.dropna().sort_index().loc[lambda item: ~item.index.duplicated(keep="last")]


def sentiment_state(
    macro_raw: dict[str, Any],
    cn_raw: dict[str, Any],
    rules: dict[str, Any],
) -> dict[str, Any]:
    vix = history_series(macro_raw["histories"]["vix"])
    vix_rank = _percentile(vix)
    vix_euphoria = 1.0 - vix_rank if vix_rank is not None else None

    margin = pd.DataFrame(cn_raw.get("margin_history", []))
    if not margin.empty:
        margin["trade_date"] = pd.to_datetime(margin["trade_date"])
        margin = margin.sort_values("trade_date").set_index("trade_date")
        balance_change = margin["financing_balance_yuan"].pct_change(5)
        buy_pressure = margin["financing_buy_yuan"] / margin["financing_balance_yuan"]
        balance_rank = _percentile(balance_change)
        buy_rank = _percentile(buy_pressure)
    else:
        balance_change = pd.Series(dtype=float)
        buy_pressure = pd.Series(dtype=float)
        balance_rank = None
        buy_rank = None

    options = pd.DataFrame(cn_raw.get("option_history", []))
    if not options.empty:
        options["trade_date"] = pd.to_datetime(options["trade_date"])
        options = options.sort_values("trade_date").set_index("trade_date")
        pcr = pd.to_numeric(options["growth_put_call_volume"], errors="coerce")
        pcr_rank = _percentile(pcr, minimum=20)
        pcr_euphoria = 1.0 - pcr_rank if pcr_rank is not None else None
    else:
        pcr = pd.Series(dtype=float)
        pcr_rank = None
        pcr_euphoria = None

    components = {
        "vix": vix_euphoria,
        "margin_balance_change_5d": balance_rank,
        "margin_buy_flow": buy_rank,
        "growth_option_put_call": pcr_euphoria,
    }
    score, coverage, missing = _weighted_observed(components, rules["tactical"]["sentiment_weights"])
    if score is None:
        label = "unavailable"
    elif score <= rules["tactical"]["thresholds"]["fearful_sentiment"]:
        label = "fearful"
    elif score >= rules["tactical"]["thresholds"]["euphoric_sentiment"]:
        label = "euphoric"
    else:
        label = "neutral"
    return {
        "state_percentile": round(score, 6) if score is not None else None,
        "label": label,
        "coverage": round(coverage, 6),
        "components": {
            "vix": {
                "value": round(float(vix.iloc[-1]), 6) if not vix.empty else None,
                "raw_percentile": round(vix_rank, 6) if vix_rank is not None else None,
                "euphoria_percentile": round(vix_euphoria, 6) if vix_euphoria is not None else None,
            },
            "margin_balance_change_5d": {
                "value": round(float(balance_change.dropna().iloc[-1]), 6) if not balance_change.dropna().empty else None,
                "euphoria_percentile": round(balance_rank, 6) if balance_rank is not None else None,
            },
            "margin_buy_flow": {
                "value": round(float(buy_pressure.dropna().iloc[-1]), 8) if not buy_pressure.dropna().empty else None,
                "euphoria_percentile": round(buy_rank, 6) if buy_rank is not None else None,
            },
            "growth_option_put_call": {
                "value": round(float(pcr.dropna().iloc[-1]), 6) if not pcr.dropna().empty else None,
                "raw_percentile": round(pcr_rank, 6) if pcr_rank is not None else None,
                "euphoria_percentile": round(pcr_euphoria, 6) if pcr_euphoria is not None else None,
            },
        },
        "missing_components": missing,
    }


def _liquidity_components(histories: dict[str, Any], cutoff: pd.Timestamp) -> dict[str, float | None]:
    series = {
        name: history_series(history).loc[lambda values: values.index <= cutoff]
        for name, history in histories.items()
    }

    def rank_level(name: str, minimum: int = 40) -> float | None:
        values = series.get(name, pd.Series(dtype=float))
        return _percentile(values, minimum=minimum)

    def rank_change(name: str, periods: int = 20, minimum: int = 40) -> float | None:
        values = series.get(name, pd.Series(dtype=float))
        return _percentile(values.diff(periods), minimum=minimum)

    real = [rank_level("tips_10y"), rank_change("tips_10y")]
    nominal = [rank_change("ust_2y"), rank_change("ust_10y"), rank_change("ust_30y")]
    dollar = [rank_change("broad_dollar")]
    credit = [rank_change("credit_spread"), rank_level("nfci", minimum=20)]

    inflation_values: list[float | None] = []
    for name in ("core_cpi", "ppi_final_demand", "core_pce"):
        values = series.get(name, pd.Series(dtype=float))
        if len(values) >= 16:
            yoy = values.pct_change(12) * 100
            inflation_values.append(_percentile(yoy, minimum=12))

    china_inflation = [
        rank_level("china_cpi_yoy", minimum=12),
        rank_level("china_ppi_yoy", minimum=12),
    ]

    def average(values: list[float | None]) -> float | None:
        observed = [float(value) for value in values if value is not None]
        return sum(observed) / len(observed) if observed else None

    return {
        "real_yields": average(real),
        "nominal_yields": average(nominal),
        "broad_dollar": average(dollar),
        "credit_conditions": average(credit),
        "inflation_impulse": average(inflation_values),
        "china_inflation_impulse": average(china_inflation),
    }


def liquidity_state(macro_raw: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    histories = macro_raw.get("histories", {})
    anchor = history_series(histories["ust_10y"])
    if len(anchor) < 50:
        raise ValueError("liquidity state requires at least 50 UST 10Y observations")
    current_cutoff = anchor.index[-1]
    prior_cutoff = anchor.index[-6] if len(anchor) >= 6 else anchor.index[0]
    current_components = _liquidity_components(histories, current_cutoff)
    prior_components = _liquidity_components(histories, prior_cutoff)
    score, coverage, missing = _weighted_observed(
        current_components, rules["tactical"]["liquidity_weights"]
    )
    prior_score, _, _ = _weighted_observed(prior_components, rules["tactical"]["liquidity_weights"])
    delta = score - prior_score if score is not None and prior_score is not None else None
    easing = [
        name
        for name, value in current_components.items()
        if value is not None and prior_components.get(name) is not None and value < prior_components[name] - 0.02
    ]
    tightening = [
        name
        for name, value in current_components.items()
        if value is not None and prior_components.get(name) is not None and value > prior_components[name] + 0.02
    ]
    return {
        "tightening_percentile": round(score, 6) if score is not None else None,
        "prior_5_session_percentile": round(prior_score, 6) if prior_score is not None else None,
        "delta_5_sessions": round(delta, 6) if delta is not None else None,
        "coverage": round(coverage, 6),
        "effective_us_observation_date": current_cutoff.strftime("%Y-%m-%d"),
        "components": {
            name: {
                "current": round(value, 6) if value is not None else None,
                "prior_5_sessions": (
                    round(prior_components.get(name), 6) if prior_components.get(name) is not None else None
                ),
            }
            for name, value in current_components.items()
        },
        "easing_components": easing,
        "tightening_components": tightening,
        "missing_components": missing,
    }


def position_decision(
    factors: dict[str, Any],
    rules: dict[str, Any],
    current_ai_pct: float,
) -> dict[str, Any]:
    growth = factors["growth"]
    momentum = factors["momentum"]
    sentiment = factors["sentiment"]
    liquidity = factors["liquidity"]
    thresholds = rules["tactical"]["thresholds"]
    minimum_coverage = float(rules["tactical"]["minimum_layer_coverage"])

    regime = growth.get("regime")
    if regime not in rules["growth"]["regimes"]:
        return {
            "action": "NO_ACTION",
            "reason": "growth regime unavailable",
            "current_ai_pct": current_ai_pct,
            "target_ai_pct": current_ai_pct,
        }
    band = rules["growth"]["regimes"][regime]
    coverage_ok = all(
        layer.get("coverage", 0.0) >= minimum_coverage
        for layer in (momentum, sentiment, liquidity)
    )
    if not coverage_ok:
        return {
            "action": "NO_ACTION",
            "reason": "one or more tactical layers are below minimum coverage",
            "base_band": band,
            "current_ai_pct": current_ai_pct,
            "target_ai_pct": current_ai_pct,
        }

    risk_off_checks = {
        "momentum_high": momentum["level_percentile"] >= thresholds["high_momentum"],
        "momentum_rolling_over": momentum["turn"] < 0,
        "sentiment_euphoric": sentiment["state_percentile"] >= thresholds["euphoric_sentiment"],
        "liquidity_tight_and_rising": (
            liquidity["tightening_percentile"] >= thresholds["tight_liquidity"]
            and liquidity["delta_5_sessions"] > 0
        ),
    }
    risk_on_checks = {
        "momentum_turn_positive": momentum["turn"] > 0,
        "momentum_confirmed": momentum["turn_positive_streak"] >= thresholds["turn_confirmation_closes"],
        "sentiment_fearful": sentiment["state_percentile"] <= thresholds["fearful_sentiment"],
        "liquidity_easing": liquidity["delta_5_sessions"] < 0,
        "easing_breadth": len(liquidity["easing_components"]) >= thresholds["minimum_easing_components"],
    }
    hard_override = (
        regime == "contraction"
        and growth.get("confidence", 0.0) >= 0.70
        and liquidity["tightening_percentile"] >= 0.90
    )
    step = float(rules["tactical"]["position"]["step_pct"])
    hard_max = float(rules["tactical"]["position"]["hard_max_pct"])
    if hard_override:
        action = "HARD_RISK_REDUCE"
        target = min(current_ai_pct, float(band["base_max_pct"]))
        rationale = "growth contraction and extreme liquidity tightening override the normal floor"
    elif all(risk_off_checks.values()):
        action = "REDUCE_TO_BASE"
        target = max(current_ai_pct - step, float(band["base_min_pct"]))
        rationale = "high momentum rolled over while sentiment is euphoric and liquidity is tightening"
    elif all(risk_on_checks.values()):
        action = "ADD"
        target = min(
            max(current_ai_pct, float(band["base_central_pct"])) + step,
            float(band["normal_max_pct"]),
            hard_max,
        )
        rationale = "momentum reversed higher from weak sentiment while liquidity tightening eased"
    else:
        action = "HOLD"
        target = current_ai_pct
        rationale = "the complete add or reduce condition set is not satisfied"
    return {
        "action": action,
        "rationale": rationale,
        "base_band": band,
        "tactical_delta_pct": round(target - current_ai_pct, 2),
        "current_ai_pct": round(current_ai_pct, 2),
        "target_ai_pct": round(target, 2),
        "risk_on_checks": risk_on_checks,
        "risk_off_checks": risk_off_checks,
        "hard_risk_override": hard_override,
    }
