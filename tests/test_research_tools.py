from __future__ import annotations

import asyncio
import json
import sys
import unittest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.config import AppConfig
from a_share_claw.context import ConversationContext
from a_share_claw.research_tools import ResearchRuntime, _pipeline_python, _validate_as_of_date


class ResearchToolsTest(unittest.TestCase):
    def _runtime(self, root: Path) -> ResearchRuntime:
        config = AppConfig.from_env(root)
        config.ensure_dirs()
        config.compiled_dir.mkdir(parents=True, exist_ok=True)
        (config.compiled_dir / "rules_L1.json").write_text(
            json.dumps({"_version": "test", "_weights": {"us": 0.6, "cn": 0.4}}),
            encoding="utf-8",
        )
        config.pipeline_dir.mkdir(parents=True, exist_ok=True)
        (config.pipeline_dir / "OPERATIONS.md").write_text(
            "# Macro\n\n## Official Run\nfixed-command-marker\n",
            encoding="utf-8",
        )
        config.research_operations_path.parent.mkdir(parents=True, exist_ok=True)
        config.research_operations_path.write_text(
            "# Industry\n\n## Active Contract\ncontract\n\n## Phase 1\ncollect evidence\n",
            encoding="utf-8",
        )
        context = ConversationContext("u", "c", "s", "local", "local", "local")
        return ResearchRuntime(config, context)

    def test_rule_and_operation_sections_are_bounded(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = self._runtime(Path(raw))
            rule = json.loads(asyncio.run(runtime.get_compiled_rule("L1", "_weights")))
            self.assertEqual(rule["rule"], {"us": 0.6, "cn": 0.4})
            operation = asyncio.run(runtime.get_operation_manual("macro", "Official Run"))
            self.assertIn("fixed-command-marker", operation)

    def test_data_audit_detects_future_observation(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = self._runtime(Path(raw))
            date = "20260713"
            config = runtime.config
            payloads = {
                config.raw_data_dir / f"raw_macro_{date}.json": {
                    "as_of_date": date,
                    "source": "akshare",
                    "_meta": {"fallback_status": "none"},
                },
                config.raw_data_dir / f"raw_macro_us_{date}.json": {
                    "as_of_date": date,
                    "source": "FRED",
                    "_meta": {"fallback_status": "none"},
                    "_provenance": {"vix": {"observation_date": "2026-07-13"}},
                },
                config.analysis_dir / f"p1_upgrade_results_{date}.json": {
                    "as_of_date": date,
                    "data_audit": {},
                },
            }
            for layer in ("L1", "L2", "L3", "composite"):
                payloads[config.scores_dir / f"scores_{layer}_{date}.json"] = {
                    "as_of_date": date,
                    "data_audit": {"fallbacks": [], "static_fallback_fields": []},
                }
            for path, data in payloads.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding="utf-8")

            clean = json.loads(asyncio.run(runtime.inspect_data_audit(date)))
            self.assertTrue(clean["official_ready"])

            us_path = config.raw_data_dir / f"raw_macro_us_{date}.json"
            us_data = json.loads(us_path.read_text(encoding="utf-8"))
            us_data["_provenance"]["vix"]["observation_date"] = "2026-07-14"
            us_path.write_text(json.dumps(us_data), encoding="utf-8")
            future = json.loads(asyncio.run(runtime.inspect_data_audit(date)))
            self.assertFalse(future["official_ready"])
            self.assertTrue(future["future_sources"])

    def test_future_date_is_rejected_before_execution(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = self._runtime(Path(raw))
            with self.assertRaises(ValueError):
                asyncio.run(runtime.inspect_data_audit("29990101"))

    def test_a_share_date_uses_market_timezone_not_owner_timezone(self) -> None:
        now_utc = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(
            _validate_as_of_date("20260714", "Asia/Shanghai", now_utc),
            "20260714",
        )
        with self.assertRaises(ValueError):
            _validate_as_of_date("20260714", "America/Los_Angeles", now_utc)

    def test_system_state_tool_flags_external_or_missing_source_paths(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = self._runtime(Path(raw))
            runtime.config.system_state_path.parent.mkdir(parents=True, exist_ok=True)
            runtime.config.system_state_path.write_text(
                json.dumps(
                    {
                        "data_audit": {
                            "source_files": [
                                {"path": "/legacy/openclaw/raw_macro_20260713.json"},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            payload = json.loads(asyncio.run(runtime.get_system_state()))
            self.assertFalse(payload["state_source_audit"]["ok"])
            self.assertEqual(
                payload["state_source_audit"]["status"],
                "legacy_external_or_missing",
            )

    def test_pipeline_runtime_prefers_standard_uv_environment(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = self._runtime(Path(raw))
            python = runtime.config.pipeline_dir / ".venv" / "bin" / "python"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            self.assertEqual(_pipeline_python(runtime.config), str(python))

    def test_pipeline_runtime_does_not_use_legacy_named_environment(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = self._runtime(Path(raw))
            legacy_python = runtime.config.pipeline_dir / ".venv313" / "bin" / "python"
            legacy_python.parent.mkdir(parents=True, exist_ok=True)
            legacy_python.write_text("", encoding="utf-8")
            self.assertEqual(_pipeline_python(runtime.config), sys.executable)

    def test_ai_runtime_requires_explicit_historical_vintage_flag(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = self._runtime(Path(raw))
            runner = AsyncMock(return_value={"ok": True})
            now = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
            with patch.object(runtime, "_run_pipeline_commands", runner):
                payload = json.loads(
                    asyncio.run(
                        runtime.run_ai_strategy(
                            "20260720",
                            allow_current_vintage_backtest=True,
                            now_utc=now,
                        )
                    )
                )
            self.assertTrue(payload["ok"])
            commands = runner.await_args.args[0]
            self.assertIn(
                ["fetch_ai_macro.py", "--date", "20260720", "--allow-current-vintage-backtest"],
                commands,
            )
            self.assertIn(
                ["compute_ai_factors.py", "--date", "20260720", "--allow-unverified-inputs"],
                commands,
            )
            self.assertIn(
                [
                    "run_ai_position.py",
                    "--date",
                    "20260720",
                    "--current-ai-pct",
                    "57.5",
                    "--allow-unverified",
                ],
                commands,
            )


if __name__ == "__main__":
    unittest.main()
