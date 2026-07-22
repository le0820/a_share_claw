from __future__ import annotations

import dataclasses
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.config import AppConfig
from a_share_claw.agent import build_model_client, InvestmentAgent
from a_share_claw.db import Storage


class ModelClientTest(unittest.IsolatedAsyncioTestCase):
    def _config(self, **overrides: object) -> AppConfig:
        base = AppConfig.from_env(Path(__file__).resolve().parents[1])
        return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]

    def test_domestic_client_bypasses_env_socks_proxy_by_default(self) -> None:
        config = self._config(
            model_base_url="https://tokenhub.tencentmaas.com/v1",
            model_api_key="sk-test",
            model_trust_env=False,
        )
        with patch.dict(os.environ, {"ALL_PROXY": "socks5://127.0.0.1:7890"}):
            client = build_model_client(config)

        assert client is not None
        self.assertFalse(client._client.trust_env)

    def test_client_respects_env_proxy_when_trust_env_enabled(self) -> None:
        config = self._config(
            model_base_url="https://tokenhub.tencentmaas.com/v1",
            model_api_key="sk-test",
            model_trust_env=True,
        )
        with patch.dict(
            os.environ,
            {
                "HTTPS_PROXY": "http://127.0.0.1:7890",
                "HTTP_PROXY": "",
                "ALL_PROXY": "",
                "https_proxy": "",
                "http_proxy": "",
                "all_proxy": "",
            },
        ):
            client = build_model_client(config)

        assert client is not None
        self.assertTrue(client._client.trust_env)

    def test_no_domestic_client_without_base_url(self) -> None:
        config = self._config(model_base_url=None, model_api_key=None)
        self.assertIsNone(build_model_client(config))

    def test_configure_model_client_ignores_socks_proxy(self) -> None:
        config = self._config(
            model_base_url="https://tokenhub.tencentmaas.com/v1",
            model_api_key="sk-test",
        )
        agent = InvestmentAgent(config, storage=object())
        with patch.dict(os.environ, {"ALL_PROXY": "socks5://127.0.0.1:7890"}):
            # Must not raise "Using SOCKS proxy, but the 'socksio' package is
            # not installed" from the tracing exporter's httpx.Client.
            agent._configure_model_client()

    async def test_macro_request_receives_routed_domain_tools(self) -> None:
        with TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            for name in ("AGENTS.md", "README.md", "IDENTITY.md", "DATA_CONTRACT.md"):
                (root / name).write_text(f"# {name}\n", encoding="utf-8")
            (root / "src" / "pipeline").mkdir(parents=True)
            (root / "src" / "pipeline" / "OPERATIONS.md").write_text("# Macro Operations\n", encoding="utf-8")
            (root / "src" / "compiled").mkdir(parents=True)
            (root / "data" / "state").mkdir(parents=True)
            (root / "data" / "state" / "system_state.json").write_text("{}", encoding="utf-8")

            config = dataclasses.replace(
                AppConfig.from_env(root),
                model_base_url=None,
                model_api_key=None,
                model_name="test-model",
                skill_dirs=(),
                plugin_dirs=(),
            )
            config.ensure_dirs()
            storage = Storage(config.database_path)
            storage.init()
            context = storage.get_or_create_context("local", "local", "local", "Local")
            investment_agent = InvestmentAgent(config, storage)
            captured: dict[str, object] = {}

            async def fake_runner(agent, message, session=None):
                captured["tools"] = {tool.name for tool in agent.tools}
                captured["instructions"] = agent.instructions
                return SimpleNamespace(final_output="ok")

            with patch.object(investment_agent, "_configure_model_client"), patch("agents.Runner.run", new=fake_runner):
                output = await investment_agent._run_agents_sdk(context, "请生成 L1/L2/L3 宏观评分")

            self.assertEqual(output, "ok")
            tools = captured["tools"]
            self.assertIn("run_macro_pipeline", tools)
            self.assertIn("inspect_data_audit", tools)
            self.assertNotIn("run_bash", tools)
            self.assertNotIn("write_text_file", tools)
            self.assertIn("workflow: macro", captured["instructions"])
            storage.close()

    async def test_real_requests_receive_only_workflow_tools_without_calling_model(self) -> None:
        with TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            for name in ("AGENTS.md", "README.md", "IDENTITY.md", "DATA_CONTRACT.md"):
                (root / name).write_text(f"# {name}\n", encoding="utf-8")
            (root / "src" / "pipeline").mkdir(parents=True)
            (root / "src" / "pipeline" / "OPERATIONS.md").write_text(
                "# Macro Operations\n",
                encoding="utf-8",
            )
            (root / "src" / "compiled").mkdir(parents=True)
            (root / "data" / "deepresearch").mkdir(parents=True)
            (root / "data" / "deepresearch" / "OPERATIONS.md").write_text(
                "# Industry Operations\n\n## Active Contract\nUse dated primary evidence.\n",
                encoding="utf-8",
            )
            (root / "data" / "state").mkdir(parents=True)
            (root / "data" / "state" / "system_state.json").write_text("{}", encoding="utf-8")

            config = dataclasses.replace(
                AppConfig.from_env(root),
                model_base_url=None,
                model_api_key=None,
                model_name="test-model",
                timezone="America/Los_Angeles",
                market_timezone="Asia/Shanghai",
                skill_dirs=(),
                plugin_dirs=(),
                enable_codex_tool=False,
            )
            config.ensure_dirs()
            storage = Storage(config.database_path)
            storage.init()
            context = storage.get_or_create_context("local", "local", "local", "Local")
            investment_agent = InvestmentAgent(config, storage)
            captured: dict[str, object] = {}

            async def fake_runner(agent, message, session=None):
                captured["tools"] = {tool.name for tool in agent.tools}
                captured["instructions"] = agent.instructions
                return SimpleNamespace(final_output="ok")

            cases = (
                (
                    "针对北京时间7月13日与美东时间7月10日的市场走势和A股最新业绩预告，运行北京时间7月14的每日评分数据管线、7月14日市场前瞻分析展望和7月13日市场总结并交由团队进行分析与分数判断。",
                    "mixed",
                    {
                        "get_market_session_status",
                        "get_system_state",
                        "run_macro_pipeline",
                        "inspect_data_audit",
                        "search_industry_research",
                        "assess_deepresearch_evidence",
                        "qveris_readonly_call",
                        "write_text_file",
                    },
                    {"run_bash"},
                ),
                (
                    "测试工具、上下文、数据接口。但不调用模型。数据交由你来分析A股7月14日早盘行情，并评估是否可以对159682、159516进行加仓？",
                    "macro",
                    {"get_system_state", "run_macro_pipeline", "inspect_data_audit"},
                    {"run_bash", "write_text_file", "search_industry_research"},
                ),
                (
                    "用过去三年数据回测159682的20日均线策略，并给最大回撤和夏普比率",
                    "quant",
                    {"get_system_state", "run_macro_pipeline", "write_text_file"},
                    {"run_bash", "generate_daily_report", "search_industry_research"},
                ),
                (
                    "个股调研：分析宁德时代2026年一季报、现金流、ROE和估值",
                    "company",
                    {
                        "get_system_state",
                        "search_industry_research",
                        "inspect_data_audit",
                        "write_text_file",
                        "assess_deepresearch_evidence",
                        "qveris_readonly_call",
                    },
                    {"run_bash", "run_macro_pipeline", "generate_daily_report"},
                ),
                (
                    "梳理半导体设备产业链、上下游和利润池",
                    "industry",
                    {
                        "get_system_state",
                        "search_industry_research",
                        "inspect_data_audit",
                        "write_text_file",
                        "assess_deepresearch_evidence",
                        "qveris_readonly_call",
                    },
                    {"run_bash", "run_macro_pipeline", "generate_daily_report"},
                ),
            )

            try:
                with patch.object(investment_agent, "_configure_model_client"), patch.object(
                    investment_agent,
                    "_optional_codex_tools",
                    side_effect=AssertionError("investment workflows must not open the broad Codex tool"),
                ), patch("agents.Runner.run", new=fake_runner):
                    for message, workflow, required, forbidden in cases:
                        with self.subTest(workflow=workflow):
                            output = await investment_agent._run_agents_sdk(context, message)
                            self.assertEqual(output, "ok")
                            tools = captured["tools"]
                            self.assertTrue(required <= tools)
                            self.assertTrue(forbidden.isdisjoint(tools))
                            instructions = captured["instructions"]
                            self.assertIn(f"workflow: {workflow}", instructions)
                            self.assertIn("默认时区：America/Los_Angeles", instructions)
                            self.assertIn("市场时区：Asia/Shanghai", instructions)
            finally:
                storage.close()


if __name__ == "__main__":
    unittest.main()
