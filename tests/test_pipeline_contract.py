from __future__ import annotations

import os
import subprocess
import sys
import unittest
import json
import csv
import math
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "pipeline"))

from pipeline_cli import parse_fetch_etf_days
from fetch_fundamental import aggregate as aggregate_fundamental
from market_math import annualized_sharpe, compound_simple_returns, simple_returns
from pipeline_contracts import contains_static_marker, future_date_fields, validate_payload
from pipeline_paths import current_date, validate_as_of_date
from pipeline_universe import load_universe
from rules_L1 import CN_WEIGHTS, US_WEIGHTS


class PipelineContractTest(unittest.TestCase):
    def test_fetch_etf_days_accepts_split_and_equals_cli_forms(self) -> None:
        self.assertEqual(parse_fetch_etf_days(["--days", "500"]), 500)
        self.assertEqual(parse_fetch_etf_days(["--days=500"]), 500)
        self.assertEqual(parse_fetch_etf_days([]), 500)

    def test_pipeline_date_boundary_uses_a_share_market_timezone(self) -> None:
        now_utc = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
        with patch.dict(
            os.environ,
            {
                "ASCLAW_TIMEZONE": "America/Los_Angeles",
                "ASCLAW_MARKET_TIMEZONE": "Asia/Shanghai",
            },
        ):
            self.assertEqual(current_date(now_utc).isoformat(), "2026-07-14")
            self.assertEqual(validate_as_of_date("20260714", now_utc=now_utc), "20260714")

    def test_scoring_rejects_future_date_before_reading_inputs(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "src" / "pipeline" / "run_scoring.py"), "--date", "29990101"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("future", proc.stderr)

    def test_report_refuses_missing_exact_score_files(self) -> None:
        with TemporaryDirectory() as raw:
            env = os.environ.copy()
            env["ASCLAW_DATA_DIR"] = raw
            proc = subprocess.run(
                [sys.executable, str(ROOT / "src" / "pipeline" / "generate_daily_report.py"), "--date", "20260713"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("MISSING", proc.stderr)

    def test_simple_return_math_does_not_compound_log_returns(self) -> None:
        returns = simple_returns([100.0, 110.0, 99.0])
        self.assertAlmostEqual(returns[0], 0.10)
        self.assertAlmostEqual(returns[1], -0.10)
        self.assertAlmostEqual(compound_simple_returns(returns), -0.01)
        self.assertNotEqual(annualized_sharpe(returns, annual_risk_free=0.02), 0.0)

    def test_fallback_detection_rejects_prefixed_and_estimated_sources(self) -> None:
        self.assertTrue(contains_static_marker({"source": "fallback (provider unavailable)"}))
        self.assertTrue(contains_static_marker({"source": "estimated residual holdings"}))
        with self.assertRaises(ValueError):
            validate_payload(
                {"as_of_date": "20260713", "_meta": {"fallback_status": "static_fallback"}},
                "20260713",
            )

    def test_future_publication_and_filing_dates_are_rejected(self) -> None:
        payload = {
            "as_of_date": "20260713",
            "publication_date": "2026-07-14",
            "nested": {"filing_date": "2026-07-15"},
        }
        future = future_date_fields(payload, "20260713")
        self.assertEqual(len(future), 2)
        with self.assertRaises(ValueError):
            validate_payload(payload, "20260713")

    def test_pipeline_universe_and_compiled_weights_are_single_sources(self) -> None:
        universe = load_universe()
        self.assertEqual(set(universe["scoring_assets"]), {"159682.SZ", "QQQ.US"})
        matrix = json.loads((ROOT / "src" / "compiled" / "weight_matrix.json").read_text(encoding="utf-8"))
        self.assertEqual(US_WEIGHTS, {
            "employment": matrix["L1_weights"]["us_factor_weights"]["employment_growth"],
            "inflation": matrix["L1_weights"]["us_factor_weights"]["inflation_wages"],
            "monetary": matrix["L1_weights"]["us_factor_weights"]["monetary_policy"],
            "financial": matrix["L1_weights"]["us_factor_weights"]["financial_conditions"],
            "tech_capex": matrix["L1_weights"]["us_factor_weights"]["tech_capex"],
        })
        self.assertEqual(CN_WEIGHTS, matrix["L1_weights"]["cn_factor_weights"])
        composite = matrix["composite_weights"]
        self.assertEqual(composite["L2"], 0.0)
        self.assertAlmostEqual(composite["L1"], 4 / 7)
        self.assertAlmostEqual(composite["L3"], 3 / 7)
        self.assertAlmostEqual(composite["L1"] + composite["L2"] + composite["L3"], 1.0)

    def test_easy_tdx_fetch_uses_context_managed_clients(self) -> None:
        source = (ROOT / "src" / "pipeline" / "fetch_etf_data.py").read_text(encoding="utf-8")
        self.assertIn("stack.enter_context(factory.from_best_host())", source)
        self.assertNotIn(".__enter__()", source)

    def test_pipeline_contains_no_embedded_constituents_and_no_ff5_stage(self) -> None:
        fundamental = (ROOT / "src" / "pipeline" / "fetch_fundamental.py").read_text(encoding="utf-8")
        p1 = (ROOT / "src" / "pipeline" / "p1_upgrade.py").read_text(encoding="utf-8")
        self.assertNotIn("TOP10_WEIGHTS", fundamental)
        self.assertNotIn("QQQ_FINANCIALS", fundamental)
        self.assertFalse((ROOT / "src" / "pipeline" / "cn_ff_regression.py").exists())
        self.assertNotIn("ASCLAW_FF5_FILE", p1)
        self.assertNotIn("ff5_loadings", p1)

    def test_fundamental_adapter_aggregates_only_observed_weight(self) -> None:
        payload = {
            "schema_version": 1,
            "as_of_date": "20260713",
            "fallback_status": "none",
            "funds": {
                "159682.SZ": {
                    "holdings": [
                        {
                            "symbol": "300750.SZ",
                            "weight": 0.8,
                            "effective_date": "2026-06-30",
                            "publication_date": "2026-07-10",
                            "source": "issuer",
                            "source_url": "https://example.test/holdings",
                        }
                    ],
                    "fundamentals": [
                        {
                            "symbol": "300750.SZ",
                            "period_end": "2026-03-31",
                            "filing_date": "2026-04-30",
                            "publication_date": "2026-04-30",
                            "source": "filing",
                            "source_url": "https://example.test/filing",
                            "roe_pct": 20.0,
                        }
                    ],
                }
            },
        }
        result = aggregate_fundamental(payload, "20260713", Path("fixture.json"))
        fund = result["funds"]["159682.SZ"]
        self.assertEqual(fund["coverage_weight"], 0.8)
        self.assertEqual(fund["missing_weight"], 0.2)
        self.assertEqual(fund["metrics"]["roe_pct"], 20.0)
        self.assertIn("without_residual_estimation", fund["aggregation_method"])

    def test_synthetic_pipeline_is_date_safe_and_does_not_rewind_state(self) -> None:
        pipeline_python = ROOT / "src" / "pipeline" / ".venv" / "bin" / "python"
        if not pipeline_python.is_file():
            self.skipTest("pipeline environment is not synchronized")
        with TemporaryDirectory() as raw:
            data_root = Path(raw)
            market_dir = data_root / "raw" / "market"
            state_dir = data_root / "state"
            market_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)

            symbols = ["159682_SZ", "QQQ_US"]
            start = datetime(2024, 1, 2)
            dates = []
            current = start
            while current <= datetime(2026, 7, 13):
                if current.weekday() < 5:
                    dates.append(current)
                current = current.fromordinal(current.toordinal() + 1)
            for symbol_index, symbol in enumerate(symbols):
                path = market_dir / f"{symbol}_daily.csv"
                with path.open("w", newline="", encoding="utf-8") as file:
                    writer = csv.writer(file)
                    writer.writerow(["trade_date", "open", "high", "low", "close", "vol", "amount"])
                    price = 100.0 + symbol_index * 5
                    for index, trade_date in enumerate(dates):
                        daily_return = 0.0003 + math.sin(index / (9 + symbol_index)) * 0.004 + symbol_index * 0.00003
                        price *= 1 + daily_return
                        writer.writerow([
                            trade_date.strftime("%Y-%m-%d"), price * 0.999, price * 1.003,
                            price * 0.997, price, 100000 + index, price * (100000 + index),
                        ])

            date = "20260713"
            generated = "2026-07-13T15:30:00Z"
            common_meta = {
                "as_of_date": date,
                "generated_at": generated,
                "source_timestamp": generated,
                "data_period": "fixture",
                "fallback_status": "none",
            }
            cn_macro = {
                "as_of_date": date,
                "source": "fixture CN publisher archive",
                "_meta": {**common_meta, "source": "fixture CN publisher archive"},
                "pmi_mfg": 50.2,
                "pmi_non_mfg": 50.5,
                "retail_sales_yoy": 3.0,
                "m2_yoy": 8.5,
                "m1_yoy": 5.0,
                "lpr_1y": 3.0,
                "lpr_5y": 3.5,
            }
            us_macro = {
                "as_of_date": date,
                "source": "fixture FRED archive",
                "_meta": {**common_meta, "source": "fixture FRED archive"},
                "vix": 18.0,
                "spx": 6000.0,
                "ust_2y": 4.0,
                "ust_10y": 4.4,
                "ust_30y": 4.8,
                "brent": 75.0,
                "cpi_us_yoy": 2.6,
                "core_cpi_yoy": 2.8,
                "ppi_us_yoy": 2.5,
                "fed_rate": 4.0,
                "nfp_actual": 120.0,
                "unemployment": 4.2,
                "jp_jgb_10y": 1.5,
                "usdjpy": 150.0,
                "credit_spread": 1.5,
                "credit_spread_change_20d": -0.12,
            }
            (data_root / "raw" / f"raw_macro_{date}.json").write_text(json.dumps(cn_macro), encoding="utf-8")
            (data_root / "raw" / f"raw_macro_us_{date}.json").write_text(json.dumps(us_macro), encoding="utf-8")
            state_path = state_dir / "system_state.json"
            state_path.write_text(json.dumps({"scores": {"as_of_date": "20260714", "composite": 4.9}}), encoding="utf-8")

            env = os.environ.copy()
            env["ASCLAW_DATA_DIR"] = str(data_root)
            commands = [
                ["p1_upgrade.py", "--date", date],
                ["run_scoring.py", "--date", date],
                ["generate_daily_report.py", "--date", date],
            ]
            for command in commands:
                proc = subprocess.run(
                    [str(pipeline_python), str(ROOT / "src" / "pipeline" / command[0]), *command[1:]],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, msg=f"{command}:\n{proc.stdout}\n{proc.stderr}")

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["scores"]["as_of_date"], "20260714")
            self.assertEqual(state["scores"]["composite"], 4.9)
            l3 = json.loads((data_root / "scores" / f"scores_L3_{date}.json").read_text(encoding="utf-8"))
            self.assertEqual(l3["sentiment"]["vix"]["score"], 3)
            self.assertIsNone(l3["sentiment"]["put_call"]["score"])
            self.assertIsNone(l3["sentiment"]["dxy"]["score"])
            self.assertEqual(l3["coverage"]["sentiment"]["coverage_ratio"], 0.5)
            self.assertIsNotNone(l3["risk"]["per_etf"]["159682.SZ"]["dd_score"])
            self.assertIn(
                "L1.CN.credit: tsf_monthly unavailable; credit score uses observed M2/M1 only",
                l3["data_audit"]["quality_notes"],
            )
            l2 = json.loads((data_root / "scores" / f"scores_L2_{date}.json").read_text(encoding="utf-8"))
            self.assertEqual(l2["status"], "disabled")
            self.assertIsNone(l2["L2_composite"])
            composite = json.loads(
                (data_root / "scores" / f"scores_composite_{date}.json").read_text(encoding="utf-8")
            )
            self.assertIsNone(composite["L2"])
            self.assertIn("L2 disabled", composite["formula"])
            report = (data_root / "reports" / f"daily_report_{date}.md").read_text(encoding="utf-8")
            self.assertIn("l2: null", report)
            self.assertIn("L2 因子层", report)
            self.assertIn("**状态**: `disabled`", report)


if __name__ == "__main__":
    unittest.main()
