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
    from ai_strategy import growth_state, momentum_state, position_decision, sentiment_state
    from fetch_ai_macro import CHINA_SERIES, SERIES
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
            },
        }
        reduce_decision = position_decision(reduce, self.rules, 60.0)
        self.assertEqual(reduce_decision["action"], "REDUCE_TO_BASE")
        self.assertEqual(reduce_decision["target_ai_pct"], 55.0)

        reduce["sentiment"]["state_percentile"] = 0.70
        incomplete = position_decision(reduce, self.rules, 60.0)
        self.assertEqual(incomplete["action"], "HOLD")

    def test_macro_source_names_are_not_mislabelled(self) -> None:
        self.assertEqual(SERIES["broad_dollar"]["id"], "DTWEXBGS")
        self.assertEqual(SERIES["ppi_final_demand"]["id"], "PPIFIS")
        self.assertEqual(SERIES["pce"]["id"], "PCEPI")
        self.assertEqual(SERIES["core_pce"]["id"], "PCEPILFE")
        self.assertNotIn("dxy", SERIES)
        self.assertIn("china_cpi_yoy", CHINA_SERIES)
        self.assertIn("china_ppi_yoy", CHINA_SERIES)


if __name__ == "__main__":
    unittest.main()
