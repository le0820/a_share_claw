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

    async def test_all_routes_only_expose_plugins_even_with_legacy_flags(self) -> None:
        import json
        from a_share_claw.research_context import tool_names_for_workflow, ResearchWorkflow
        from a_share_claw.data_plugins import default_registry
        with TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            for name in ("AGENTS.md", "README.md", "IDENTITY.md", "DATA_CONTRACT.md"):
                (root / name).write_text(f"# {name}\n", encoding="utf-8")
            config = dataclasses.replace(AppConfig.from_env(root), model_base_url=None, model_api_key=None,
                                         skill_dirs=(), plugin_dirs=(), enable_bash=True, enable_codex_tool=True)
            config.ensure_dirs()
            config.research_operations_path.parent.mkdir(parents=True, exist_ok=True)
            config.research_operations_path.write_text("# Core Research Operations v1\n")
            config.system_state_path.parent.mkdir(parents=True, exist_ok=True)
            config.system_state_path.write_text('{"secret_marker":"LEGACY_DATA_MUST_NOT_ENTER"}')
            storage = Storage(config.database_path)
            storage.init()
            context = storage.get_or_create_context("local", "local", "local", "Local")
            investment_agent = InvestmentAgent(config, storage)
            investment_agent.memory.remember(context.user_id, "old", "MEMORY_MUST_NOT_ENTER", [])
            captured = {}

            async def invoke(tool, arguments):
                from agents.tool_context import ToolContext
                ctx = ToolContext(context=None, tool_name=tool.name, tool_call_id="test-call", tool_arguments=arguments)
                return await tool.on_invoke_tool(ctx, arguments)

            async def fake_runner(agent, message, **kwargs):
                self.assertNotIn("session", kwargs)  # old SDK tool evidence must not be replayed
                captured["tools"] = {tool.name: tool for tool in agent.tools}
                captured["instructions"] = agent.instructions
                self.assertEqual(agent.mcp_servers, [])
                self.assertNotIn("LEGACY_DATA_MUST_NOT_ENTER", agent.instructions)
                self.assertNotIn("MEMORY_MUST_NOT_ENTER", agent.instructions)
                fetch = captured["tools"]["fetch_data"]
                denied = json.loads(await invoke(fetch, '{"requirement_id":"rates"}'))
                self.assertEqual(denied["error_code"], "plan_required")
                plan = {"framework": "Study rates before drawing a conclusion", "requirements": [{
                    "requirement_id": "rates", "provider": "fred", "capability": "macro.series",
                    "as_of_date": "2026-08-01", "params": {"series_id": "DGS10", "start_date": "2026-07-01"}}]}
                await invoke(captured["tools"]["plan_data"], json.dumps({"plan_json": json.dumps(plan)}))
                result = json.loads(await invoke(fetch, '{"requirement_id":"rates"}'))
                self.assertEqual(result["error_code"], "not_configured")
                return SimpleNamespace(final_output="Research framework with an explicit data gap")

            cases = [("你好", "general"), ("每日评分和公司财报", "mixed"),
                     ("L1/L2/L3宏观评分", "macro"), ("回测最大回撤", "quant"),
                     ("个股调研现金流", "company"), ("半导体产业链", "industry")]
            providers = default_registry().snapshot({})
            with patch.object(investment_agent, "_configure_model_client"), \
                 patch("a_share_claw.data_plugins.core.Registry.snapshot", return_value=providers), \
                 patch("agents.Runner.run", new=fake_runner), \
                 patch("a_share_claw.mcp.enter_mcp_servers", side_effect=AssertionError("No MCP")), \
                 patch("urllib.request.build_opener", side_effect=AssertionError("No network without credentials")):
                for message, workflow in cases:
                    with self.subTest(workflow=workflow):
                        output = await investment_agent._run_agents_sdk(context, message)
                        self.assertIn('"official_output_allowed": false', output)
                        self.assertIn("workflow: " + workflow, captured["instructions"])
                        self.assertEqual(set(captured["tools"]), set(tool_names_for_workflow(ResearchWorkflow(workflow))))
                        self.assertEqual(len(captured["tools"]), 6)
            self.assertEqual(investment_agent._optional_codex_tools(), [])
            storage.close()


if __name__ == "__main__":
    unittest.main()
