from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "src" / "pipeline"
sys.path.insert(0, str(PIPELINE))

try:
    import numpy as np
    import pandas as pd
except ImportError:  # The app runtime and pipeline intentionally use separate venvs.
    np = None
    pd = None

if pd is not None:
    from ai_strategy import growth_state, liquidity_state, momentum_state, position_decision, sentiment_state
    from fetch_ai_macro import CHINA_SERIES, SERIES, _append_newer_observations
    from fetch_ai_growth import _company_metrics


@unittest.skipIf(pd is None, "AI pipeline dependencies live in src/pipeline/.venv")
class AIStrategyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = json.loads(
            (ROOT / "src" / "compiled" / "ai_strategy_rules.json").read_text(encoding="utf-8")
        )

    def test_growth_expansion_uses_only_observed_weight(self) -> None:
        raw = {
            "aggregate": {
                "capex": {"yoy": 0.30, "companies_observed": 5},
                "operating_cash_flow": {"yoy": 0.20, "companies_observed": 5},
                "revenue": {"yoy": 0.15, "companies_observed": 5},
                "operating_income": {"yoy": 0.25},
                "funding": {
                    "companies_observed": 5,
                    "capex_to_ocf": 0.70,
                    "capex_growth_minus_ocf_growth": 0.10,
                },
            },
            "data_audit": {"company_count": 5, "requested_company_count": 5},
        }
        state = growth_state(raw, self.rules)
        self.assertEqual(state["regime"], "expansion")
        self.assertAlmostEqual(state["coverage"], 0.80)
        self.assertIn("gpu_price_availability", state["missing_components"])
        self.assertIn("token_economics", state["missing_components"])

    def test_sec_metric_selector_prefers_latest_tag_not_first_tag(self) -> None:
        def row(start: str, end: str, filed: str, value: float) -> dict:
            return {
                "start": start,
                "end": end,
                "filed": filed,
                "form": "10-Q",
                "val": value,
            }

        old = [
            row("2016-01-01", "2016-03-31", "2016-04-30", 10.0),
            row("2017-01-01", "2017-03-31", "2017-04-30", 12.0),
        ]
        current = [
            row("2025-01-01", "2025-03-31", "2025-04-30", 20.0),
            row("2026-01-01", "2026-03-31", "2026-04-30", 30.0),
        ]
        payload = {
            "facts": {
                "us-gaap": {
                    "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": old}},
                    "PaymentsToAcquireProductiveAssets": {"units": {"USD": current}},
                }
            }
        }
        metrics = _company_metrics(payload, "20260618")
        self.assertEqual(metrics["capex"]["tag"], "PaymentsToAcquireProductiveAssets")
        self.assertEqual(metrics["capex"]["period_end"], "2026-03-31")
        self.assertEqual(metrics["capex"]["yoy"], 0.5)

    def test_momentum_implements_12_minus_1_month_return(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=420)
        sector = pd.Series(100.0 * np.exp(np.arange(420) * 0.001), index=dates)
        benchmark = pd.Series(100.0 * np.exp(np.arange(420) * 0.0005), index=dates)
        state = momentum_state(sector, benchmark, self.rules)
        expected = math.exp(0.001 * (252 - 21)) - 1.0
        observed = state["components"]["return_12m_ex_latest_month"]["value"]
        self.assertAlmostEqual(observed, expected, places=6)
        self.assertEqual(state["coverage"], 1.0)

    def test_vix_high_means_fear_not_euphoria(self) -> None:
        dates = pd.bdate_range("2026-01-02", periods=100)

        def macro(last: float) -> dict:
            values = np.linspace(15.0, 25.0, len(dates))
            values[-1] = last
            return {
                "histories": {
                    "vix": {
                        "observations": [
                            {"observation_date": date.strftime("%Y-%m-%d"), "value": float(value)}
                            for date, value in zip(dates, values)
                        ]
                    }
                }
            }

        empty_cn = {"margin_history": [], "option_history": []}
        fearful = sentiment_state(macro(40.0), empty_cn, self.rules)
        euphoric = sentiment_state(macro(10.0), empty_cn, self.rules)
        self.assertLess(fearful["state_percentile"], euphoric["state_percentile"])
        self.assertEqual(fearful["components"]["vix"]["euphoria_percentile"], 0.0)

    def test_complete_add_and_reduce_conditions_are_conjunctive(self) -> None:
        common = {
            "growth": {"regime": "expansion", "confidence": 0.8},
            "momentum": {"coverage": 1.0},
            "sentiment": {"coverage": 1.0},
            "liquidity": {"coverage": 1.0},
        }
        add = {
            **common,
            "momentum": {
                **common["momentum"],
                "level_percentile": 0.30,
                "turn": 0.25,
                "turn_positive_streak": 3,
            },
            "sentiment": {**common["sentiment"], "state_percentile": 0.10},
            "liquidity": {
                **common["liquidity"],
                "tightening_percentile": 0.40,
                "delta_5_sessions": -0.10,
                "easing_components": ["real_yields", "broad_dollar"],
                "absolute_yield_validation": {"passed": True},
                "brent_inflation_veto": {"clear": True},
            },
        }
        add_decision = position_decision(add, self.rules, 57.5)
        self.assertEqual(add_decision["action"], "ADD")
        self.assertEqual(add_decision["target_ai_pct"], 62.5)

        reduce = {
            **common,
            "momentum": {
                **common["momentum"],
                "level_percentile": 0.90,
                "turn": -0.25,
                "turn_positive_streak": 0,
            },
            "sentiment": {**common["sentiment"], "state_percentile": 0.90},
            "liquidity": {
                **common["liquidity"],
                "tightening_percentile": 0.80,
                "delta_5_sessions": 0.10,
                "easing_components": [],
                "absolute_yield_validation": {"passed": False},
                "brent_inflation_veto": {"clear": True},
            },
        }
        reduce_decision = position_decision(reduce, self.rules, 60.0)
        self.assertEqual(reduce_decision["action"], "REDUCE_TO_BASE")
        self.assertEqual(reduce_decision["target_ai_pct"], 55.0)

        reduce["sentiment"]["state_percentile"] = 0.70
        incomplete = position_decision(reduce, self.rules, 60.0)
        self.assertEqual(incomplete["action"], "HOLD")

        add["liquidity"]["absolute_yield_validation"] = {"passed": False}
        self.assertEqual(position_decision(add, self.rules, 57.5)["action"], "HOLD")
        add["liquidity"]["absolute_yield_validation"] = {"passed": True}
        add["liquidity"]["brent_inflation_veto"] = {"clear": False}
        self.assertEqual(position_decision(add, self.rules, 57.5)["action"], "HOLD")

    def test_macro_source_names_are_not_mislabelled(self) -> None:
        self.assertEqual(SERIES["broad_dollar"]["id"], "DTWEXBGS")
        self.assertEqual(SERIES["ppi_final_demand"]["id"], "PPIFIS")
        self.assertEqual(SERIES["pce"]["id"], "PCEPI")
        self.assertEqual(SERIES["core_pce"]["id"], "PCEPILFE")
        self.assertEqual(SERIES["brent"]["id"], "DCOILBRENTEU")
        self.assertNotIn("dxy", SERIES)
        self.assertIn("china_cpi_yoy", CHINA_SERIES)
        self.assertIn("china_ppi_yoy", CHINA_SERIES)

    def test_treasury_only_appends_observations_newer_than_fred(self) -> None:
        base = [
            {"observation_date": "2026-07-20", "value": 4.60},
        ]
        supplement = [
            {"observation_date": "2026-07-20", "value": 4.61},
            {"observation_date": "2026-07-21", "value": 4.63},
        ]
        merged, used = _append_newer_observations(base, supplement)
        self.assertTrue(used)
        self.assertEqual(merged[0]["value"], 4.60)
        self.assertEqual(merged[-1], {"observation_date": "2026-07-21", "value": 4.63})

    def test_absolute_yields_and_brent_gate_risk_on(self) -> None:
        dates = pd.bdate_range("2026-01-02", periods=100)

        def history(values):
            return {
                "observations": [
                    {"observation_date": date.strftime("%Y-%m-%d"), "value": float(value)}
                    for date, value in zip(dates, values)
                ]
            }

        def macro(*, long_end_rises: bool = False, brent_shock: bool = False) -> dict:
            values = np.linspace(4.0, 4.5, len(dates))
            yields = {name: values.copy() for name in ("ust_2y", "ust_10y", "ust_30y", "tips_10y")}
            for series in yields.values():
                series[-6] = 4.50
                series[-1] = 4.45
            if long_end_rises:
                yields["ust_30y"][-1] = 4.51
            brent = np.full(len(dates), 70.0)
            brent[-6] = 70.0
            brent[-1] = 74.0 if brent_shock else 71.0
            return {
                "histories": {
                    **{name: history(series) for name, series in yields.items()},
                    "broad_dollar": history(np.linspace(120.0, 119.0, len(dates))),
                    "credit_spread": history(np.linspace(1.6, 1.5, len(dates))),
                    "nfci": history(np.linspace(-0.4, -0.5, len(dates))),
                    "core_cpi": history(np.linspace(300.0, 320.0, len(dates))),
                    "ppi_final_demand": history(np.linspace(120.0, 130.0, len(dates))),
                    "core_pce": history(np.linspace(110.0, 120.0, len(dates))),
                    "china_cpi_yoy": history(np.linspace(1.0, 1.5, len(dates))),
                    "china_ppi_yoy": history(np.linspace(0.0, 1.0, len(dates))),
                    "brent": history(brent),
                }
            }

        clean = liquidity_state(macro(), self.rules)
        self.assertTrue(clean["absolute_yield_validation"]["passed"])
        self.assertTrue(clean["brent_inflation_veto"]["clear"])

        long_end = liquidity_state(macro(long_end_rises=True), self.rules)
        self.assertFalse(long_end["absolute_yield_validation"]["passed"])
        self.assertEqual(long_end["absolute_yield_validation"]["series"]["ust_30y"]["delta_bp"], 1.0)

        oil_shock = liquidity_state(macro(brent_shock=True), self.rules)
        self.assertTrue(oil_shock["brent_inflation_veto"]["triggered"])

        missing_brent = macro()
        missing_brent["histories"]["brent"]["observations"].pop()
        unavailable = liquidity_state(missing_brent, self.rules)
        self.assertIsNone(unavailable["brent_inflation_veto"]["triggered"])
        self.assertFalse(unavailable["brent_inflation_veto"]["clear"])


if __name__ == "__main__":
    unittest.main()
