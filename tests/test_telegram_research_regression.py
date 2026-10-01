from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.config import AppConfig
from a_share_claw.context import ConversationContext
from a_share_claw.mcp import load_mcp_specs
from a_share_claw.research_context import (
    ResearchWorkflow,
    build_research_context,
    tool_names_for_workflow,
)
from a_share_claw.research_tools import ResearchRuntime
from a_share_claw.web_search import search_web


ROOT = Path(__file__).resolve().parents[1]
REAL_TELEGRAM_REQUEST = (
    "针对北京时间7月13日与美东时间7月10日的市场走势和A股最新业绩预告，"
    "运行北京时间7月14的每日评分数据管线、7月14日市场前瞻分析展望和"
    "7月13日市场总结并交由团队进行分析与分数判断。"
)


class TelegramResearchRegressionTest(unittest.TestCase):
    def _runtime(self, root: Path) -> tuple[AppConfig, ConversationContext, ResearchRuntime]:
        for name in ("AGENTS.md", "README.md", "IDENTITY.md", "DATA_CONTRACT.md"):
            (root / name).write_text(f"# {name}\n", encoding="utf-8")
        (root / "src" / "pipeline").mkdir(parents=True)
        (root / "src" / "pipeline" / "OPERATIONS.md").write_text(
            "# Macro Operations\nmacro-regression-marker\n",
            encoding="utf-8",
        )
        (root / "src" / "compiled").mkdir(parents=True)
        (root / "src" / "a_share_claw").mkdir(parents=True)
        (root / "src" / "a_share_claw" / "RESEARCH_OPERATIONS.md").write_text(
            "# Deep Research\n\n"
            "## Active Contract\ndeepresearch-regression-marker\n\n"
            "## 执行协议\nPLAN -> EVIDENCE_GATE -> COMPUTE_OR_SYNTHESIZE -> EVALUATE -> ARCHIVE -> PUBLISH\n",
            encoding="utf-8",
        )
        (root / "data" / "state").mkdir(parents=True)
        (root / "data" / "state" / "system_state.json").write_text("{}", encoding="utf-8")
        with patch.dict(
            os.environ,
            {
                "ASCLAW_DATA_DIR": str(root / "data"),
                "ASCLAW_MARKET_TIMEZONE": "Asia/Shanghai",
            },
            clear=False,
        ):
            config = AppConfig.from_env(root)
        config = dataclasses.replace(
            config,
            mcp_config_path=ROOT / ".mcp.example.json",
            market_timezone="Asia/Shanghai",
        )
        config.ensure_dirs()
        context = ConversationContext("owner", "chat", "session", "telegram", "100", "chat")
        return config, context, ResearchRuntime(config, context)

    def test_real_request_uses_explicit_mixed_slice_without_bash_or_model(self) -> None:
        with TemporaryDirectory() as raw:
            config, context, _ = self._runtime(Path(raw))
            bundle = build_research_context(config, context, REAL_TELEGRAM_REQUEST)
            tools = tool_names_for_workflow(bundle.workflow)
            mcp_specs = load_mcp_specs(config, workflow=bundle.workflow.value)

            self.assertEqual(bundle.workflow, ResearchWorkflow.MIXED)
            self.assertEqual(bundle.routing_reason, "macro_plus_deepresearch")
            self.assertIn("src/pipeline/OPERATIONS.md", bundle.loaded_files)
            self.assertIn("src/a_share_claw/RESEARCH_OPERATIONS.md", bundle.loaded_files)
            self.assertNotIn("data/deepresearch/OPERATIONS.md", bundle.loaded_files)
            self.assertIn("active_slices: macro, deepresearch", bundle.instructions)
            self.assertIn("fetch_data", tools)
            self.assertNotIn("run_macro_pipeline", tools)
            self.assertIn("get_market_session_status", tools)
            self.assertIn("plan_data", tools)
            self.assertNotIn("qveris_readonly_call", tools)
            self.assertNotIn("run_bash", tools)
            self.assertEqual(set(mcp_specs), {"tavily", "qveris"})

    def test_missing_audit_maps_to_pre_market_intraday_and_post_close_actions(self) -> None:
        with TemporaryDirectory() as raw:
            _, _, runtime = self._runtime(Path(raw))
            cases = (
                (datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc), "pre_market", "WAIT_FOR_CLOSE", False),
                (datetime(2026, 7, 14, 5, 53, tzinfo=timezone.utc), "trading", "WAIT_FOR_CLOSE", False),
                (datetime(2026, 7, 14, 7, 30, tzinfo=timezone.utc), "post_close", "RUN_MACRO_PIPELINE", True),
            )

            for now_utc, phase, action, should_run in cases:
                with self.subTest(phase=phase):
                    audit = json.loads(
                        asyncio.run(runtime.inspect_data_audit("20260714", now_utc=now_utc))
                    )
                    execution = audit["execution"]
                    self.assertEqual(execution["market_session"]["phase"], phase)
                    self.assertEqual(execution["action"], action)
                    self.assertEqual(execution["pipeline_should_run"], should_run)
                    self.assertEqual(
                        execution["missing_files_mean"],
                        "not_generated_or_not_yet_run",
                    )

    def test_tool_layer_blocks_before_close_and_executes_after_close_without_real_scripts(self) -> None:
        with TemporaryDirectory() as raw:
            _, _, runtime = self._runtime(Path(raw))
            fake_commands = AsyncMock(return_value={"ok": True, "commands": []})

            with patch.object(runtime, "_run_pipeline_commands", new=fake_commands):
                pre_market = json.loads(
                    asyncio.run(
                        runtime.run_macro_pipeline(
                            "20260714",
                            now_utc=datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc),
                        )
                    )
                )
                intraday = json.loads(
                    asyncio.run(
                        runtime.run_macro_pipeline(
                            "20260714",
                            now_utc=datetime(2026, 7, 14, 5, 53, tzinfo=timezone.utc),
                        )
                    )
                )
                fake_commands.assert_not_awaited()

                post_close = json.loads(
                    asyncio.run(
                        runtime.run_macro_pipeline(
                            "20260714",
                            now_utc=datetime(2026, 7, 14, 7, 30, tzinfo=timezone.utc),
                        )
                    )
                )

            self.assertEqual(pre_market["status"], "blocked_by_market_session")
            self.assertEqual(pre_market["market_session"]["phase"], "pre_market")
            self.assertEqual(intraday["status"], "blocked_by_market_session")
            self.assertEqual(intraday["market_session"]["phase"], "trading")
            self.assertTrue(post_close["ok"])
            fake_commands.assert_awaited_once()
            commands = fake_commands.await_args.args[0]
            self.assertEqual(commands[0], ["fetch_etf_data.py", "--days", "500"])

    def test_empty_builtin_search_is_structured_no_results_not_unconfigured(self) -> None:
        empty_backend = AsyncMock(return_value=[])
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}, clear=False), patch(
            "a_share_claw.web_search._search_duckduckgo",
            new=empty_backend,
        ):
            payload = json.loads(asyncio.run(search_web("A股 2026年7月 业绩预告", 8)))

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "no_results")
        self.assertEqual(payload["backend"], "duckduckgo_html")
        self.assertEqual(payload["result_count"], 0)
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["reason"], "no_results")
        self.assertIn("not evidence", payload["instruction"])


if __name__ == "__main__":
    unittest.main()
