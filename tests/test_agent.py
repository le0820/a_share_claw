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

    async def test_all_chat_routes_enter_tool_free_core_even_with_legacy_flags(self) -> None:
        import json
        from test_harness import ROOT, DAY
        from test_harness_framework import review, proposal
        from test_harness_research import spec
        from test_sdk_research import configured
        from a_share_claw.harness.research import RoleRequest
        from a_share_claw.harness.contracts import Scope
        from a_share_claw.harness.trace import TraceRepository
        with TemporaryDirectory() as raw_dir:
            data=Path(raw_dir)
            config=configured(dataclasses.replace(AppConfig.from_env(ROOT),data_dir=data,database_path=data/"trace.sqlite",
                enable_bash=True,enable_codex_tool=True))
            storage=Storage(config.database_path);storage.init()
            context=storage.get_or_create_context("local","local","local","Local")
            agent=InvestmentAgent(config,storage)
            agent.memory.remember(context.user_id,"old","MEMORY_MUST_NOT_ENTER",[])
            storage.add_message(context.conversation_id,"assistant","OLD_TOOL_HISTORY_MUST_NOT_ENTER")
            captured=[]
            async def runner(sdk_agent,message,**kwargs):
                self.assertNotIn("session",kwargs);self.assertEqual(sdk_agent.tools,[])
                self.assertEqual(sdk_agent.mcp_servers,[]);self.assertEqual(sdk_agent.handoffs,[])
                self.assertNotIn("MEMORY_MUST_NOT_ENTER",sdk_agent.instructions+message)
                self.assertNotIn("OLD_TOOL_HISTORY_MUST_NOT_ENTER",sdk_agent.instructions+message)
                entry=json.loads(message);captured.append(entry)
                result=review(RoleRequest(message,30)) if "candidate" in entry else proposal(
                    {"research_spec":spec()} if entry["workflow"] in {"company","industry"} else {})
                return SimpleNamespace(final_output=json.dumps(result))
            cases=[("你好","general"),("每日评分和公司财报","mixed"),("L1/L2/L3宏观评分","macro"),
                ("回测最大回撤","quant"),("个股调研现金流","company"),("半导体产业链","industry"),("四季度市场展望","outlook")]
            with patch("agents.Runner.run",new=runner), \
                 patch("a_share_claw.data_plugins.core.Registry.snapshot",side_effect=AssertionError("No pre-B plugins")), \
                 patch("a_share_claw.mcp.enter_mcp_servers",side_effect=AssertionError("No MCP")), \
                 patch("urllib.request.build_opener",side_effect=AssertionError("No source network")):
                for message,workflow in cases:
                    with self.subTest(workflow=workflow):
                        out=await agent.run_result(context,DAY+" "+message)
                        self.assertFalse(out.official_output_allowed);self.assertEqual(out.action,"NO_ACTION")
                        trace=TraceRepository(storage).read(out.run_id,Scope.from_context(ROOT,context))
                        self.assertEqual(next(v["detail"]["workflow"] for v in trace["run_steps"] if v["stage"]=="route"),workflow)
                        self.assertEqual(trace["tool_calls"],[])
            self.assertEqual(len(captured),14);self.assertEqual(agent._optional_codex_tools(),[])
            storage.close()


if __name__ == "__main__":
    unittest.main()
