#!/usr/bin/env python3
"""Compute dated momentum and quantitative risk diagnostics from easy-tdx bars.

FF5 and CN style-factor regressions are intentionally excluded: the workspace
does not currently have an official, reproducible factor-file source contract.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline_contracts import utc_now_iso
from pipeline_paths import ANALYSIS_DIR, ensure_runtime_dirs
from pipeline_universe import market_csv_path, scoring_assets


ROLLING_WINDOW = 60
MIN_OBSERVATIONS = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute momentum, VaR/CVaR, drawdown and correlation for an explicit as-of date."
    )
    parser.add_argument("--date", required=True, help="As-of date in YYYYMMDD format")
    return parser.parse_args()


def load_prices(symbol: str, as_of: pd.Timestamp) -> tuple[pd.Series, dict[str, object]]:
    path = market_csv_path(symbol)
    if not path.is_file():
        raise SystemExit(f"missing easy-tdx market file for {symbol}: {path}")
    frame = pd.read_csv(path)
    required = {"trade_date", "close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit(f"{symbol} market file missing columns: {', '.join(missing)}")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame = frame.loc[frame["trade_date"] <= as_of].sort_values("trade_date")
    frame = frame.drop_duplicates("trade_date", keep="last")
    close = pd.to_numeric(frame["close"], errors="coerce")
    prices = pd.Series(close.to_numpy(), index=frame["trade_date"], name=symbol).dropna()
    if len(prices) < MIN_OBSERVATIONS + 1:
        raise SystemExit(
            f"{symbol} has only {len(prices)} observations by {as_of:%Y-%m-%d}; "
            f"need at least {MIN_OBSERVATIONS + 1}"
        )
    return prices, {
        "path": str(path),
        "source": "easy-tdx",
        "first_trade_date": prices.index.min().strftime("%Y-%m-%d"),
        "last_trade_date": prices.index.max().strftime("%Y-%m-%d"),
        "rows_used": len(prices),
        "filtered_to_as_of_date": True,
        "fallback_status": "none",
    }


def historical_var_cvar(returns: pd.Series, confidence: float) -> tuple[float, float]:
    quantile = float(returns.quantile(1 - confidence))
    tail = returns.loc[returns <= quantile]
    cvar = float(tail.mean()) if not tail.empty else quantile
    return abs(quantile), abs(cvar)


def drawdown_metrics(prices: pd.Series) -> dict[str, object]:
    wealth = prices / prices.iloc[0]
    running_peak = wealth.cummax()
    drawdown = wealth / running_peak - 1
    trough_position = int(np.argmin(drawdown.to_numpy()))
    peak_position = int(np.argmax(wealth.iloc[: trough_position + 1].to_numpy()))
    peak_date = wealth.index[peak_position]
    trough_date = wealth.index[trough_position]
    peak_level = float(running_peak.iloc[trough_position])
    recovered = wealth.iloc[trough_position:].loc[lambda values: values >= peak_level]
    recovery_date = recovered.index[0].strftime("%Y-%m-%d") if not recovered.empty else None
    return {
        "max_drawdown": round(abs(float(drawdown.iloc[trough_position])), 6),
        "peak_date": peak_date.strftime("%Y-%m-%d"),
        "trough_date": trough_date.strftime("%Y-%m-%d"),
        "recovery_date": recovery_date,
    }


def momentum_metrics(prices: pd.Series) -> dict[str, float | None]:
    def period_return(periods: int) -> float | None:
        if len(prices) <= periods:
            return None
        return round(float(prices.iloc[-1] / prices.iloc[-periods - 1] - 1), 6)

    twelve_minus_one = None
    if len(prices) >= 253:
        twelve_minus_one = round(float(prices.iloc[-22] / prices.iloc[-253] - 1), 6)
    return {
        "return_1m": period_return(21),
        "return_3m": period_return(63),
        "return_6m": period_return(126),
        "return_12m": period_return(252),
        "return_12m_excluding_latest_month": twelve_minus_one,
    }


def risk_score(value: float, thresholds: tuple[float, float, float, float]) -> float:
    if value <= thresholds[0]:
        return 5.0
    if value <= thresholds[1]:
        return 4.0
    if value <= thresholds[2]:
        return 3.0
    if value <= thresholds[3]:
        return 2.0
    return 1.0


def main() -> None:
    args = parse_args()
    as_of = pd.to_datetime(args.date, format="%Y%m%d", errors="raise")
    date = as_of.strftime("%Y%m%d")
    ensure_runtime_dirs()

    assets = scoring_assets()
    price_series: dict[str, pd.Series] = {}
    source_files: dict[str, dict[str, object]] = {}
    individual: dict[str, dict[str, object]] = {}
    l3_scores: dict[str, dict[str, object]] = {}

    for symbol, spec in assets.items():
        prices, audit = load_prices(symbol, as_of)
        price_series[symbol] = prices
        source_files[symbol] = audit
        returns = prices.pct_change().dropna()
        recent = returns.iloc[-ROLLING_WINDOW:]
        var95, cvar95 = historical_var_cvar(recent, 0.95)
        var99, cvar99 = historical_var_cvar(recent, 0.99)
        ann_volatility = float(returns.std(ddof=1) * np.sqrt(252))
        annualized_mean_return = float(returns.mean() * 252)
        rolling_var95 = returns.rolling(ROLLING_WINDOW).quantile(0.05).abs().dropna()
        if len(rolling_var95) >= 40:
            recent_var = float(rolling_var95.iloc[-20:].mean())
            prior_var = float(rolling_var95.iloc[-40:-20].mean())
            var_trend = recent_var / prior_var - 1 if prior_var else 0.0
        else:
            var_trend = 0.0
        drawdown = drawdown_metrics(prices)
        individual[symbol] = {
            "name": spec["name"],
            "observations": len(returns),
            "annualized_mean_return": round(annualized_mean_return, 6),
            "annualized_volatility": round(ann_volatility, 6),
            "return_to_volatility": round(annualized_mean_return / ann_volatility, 4) if ann_volatility else None,
            "sharpe_ratio": None,
            "sharpe_status": "disabled_no_official_risk_free_series",
            "var": {
                "window": ROLLING_WINDOW,
                "historical_var_95_1d": round(var95, 6),
                "historical_cvar_95_1d": round(cvar95, 6),
                "historical_var_99_1d": round(var99, 6),
                "historical_cvar_99_1d": round(cvar99, 6),
                "var_95_trend_20d": round(var_trend, 6),
            },
            "drawdown": drawdown,
            "momentum": momentum_metrics(prices),
        }
        score_parts = {
            "var_score": risk_score(var95, (0.015, 0.0225, 0.03, 0.04)),
            "cvar_score": risk_score(cvar95, (0.02, 0.03, 0.04, 0.055)),
            "dd_score": risk_score(float(drawdown["max_drawdown"]), (0.05, 0.10, 0.15, 0.25)),
            "var_trend_score": risk_score(max(var_trend, -1.0), (-0.15, 0.0, 0.15, 0.35)),
        }
        l3_scores[symbol] = {
            "name": spec["name"],
            **score_parts,
            "avg_risk_score": round(float(np.mean(list(score_parts.values()))), 2),
        }

    aligned = pd.concat(
        {symbol: prices.pct_change() for symbol, prices in price_series.items()}, axis=1, join="inner"
    ).dropna()
    if len(aligned) < ROLLING_WINDOW:
        raise SystemExit(f"portfolio has only {len(aligned)} aligned returns; need {ROLLING_WINDOW}")
    weights = np.repeat(1 / len(aligned.columns), len(aligned.columns))
    portfolio_returns = aligned.dot(weights)
    portfolio_recent = portfolio_returns.iloc[-ROLLING_WINDOW:]
    portfolio_var95, portfolio_cvar95 = historical_var_cvar(portfolio_recent, 0.95)
    portfolio_var99, portfolio_cvar99 = historical_var_cvar(portfolio_recent, 0.99)
    portfolio_risk_score = round(float(np.mean([item["avg_risk_score"] for item in l3_scores.values()])), 2)

    generated_at = utc_now_iso()
    output = {
        "schema_version": 2,
        "generated_at": generated_at,
        "as_of_date": date,
        "source": "easy-tdx dated daily bars",
        "fallback_status": "none",
        "model_status": {
            "ff5": "disabled_no_official_source_contract",
            "cn_style_factor_regression": "disabled_no_official_source_contract",
        },
        "data_audit": {
            "source_files": source_files,
            "source_timestamp": generated_at,
            "release_date": None,
            "filtered_to_as_of_date": True,
            "fallback_status": "none",
        },
        "individual": individual,
        "portfolio": {
            "symbols": list(aligned.columns),
            "weighting": "equal_weight_diagnostic",
            "aligned_observations": len(aligned),
            "historical_var_95_1d": round(portfolio_var95, 6),
            "historical_cvar_95_1d": round(portfolio_cvar95, 6),
            "historical_var_99_1d": round(portfolio_var99, 6),
            "historical_cvar_99_1d": round(portfolio_cvar99, 6),
            "correlation_matrix": aligned.corr().round(4).to_dict(),
        },
        "l3_risk_scores": l3_scores,
        "portfolio_risk_score": portfolio_risk_score,
    }
    output_path = ANALYSIS_DIR / f"p1_upgrade_results_{date}.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[p1] wrote momentum/risk diagnostics for {len(individual)} assets -> {output_path}")


if __name__ == "__main__":
    main()
