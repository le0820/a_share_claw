from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.config import AppConfig
from a_share_claw.mcp import enter_mcp_servers, load_mcp_specs, resolve_mcp_env


ROOT = Path(__file__).resolve().parents[1]


class McpPolicyTest(unittest.TestCase):
    def test_mcp_server_requires_explicit_allowlist(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = AppConfig.from_env(root)
            config.mcp_config_path.write_text(
                json.dumps({"mcpServers": {"unsafe": {"command": "unused"}}}),
                encoding="utf-8",
            )

            async def run() -> None:
                async with AsyncExitStack() as stack:
                    await enter_mcp_servers(config, stack)

            with self.assertRaisesRegex(ValueError, "allowedTools"):
                asyncio.run(run())

    def test_deepresearch_servers_are_selected_for_mixed_company_and_industry(self) -> None:
        base = AppConfig.from_env(ROOT)
        config = dataclasses.replace(base, mcp_config_path=ROOT / ".mcp.example.json")

        for workflow in ("mixed", "company", "industry"):
            with self.subTest(workflow=workflow):
                specs = load_mcp_specs(config, workflow=workflow)
                self.assertEqual(set(specs), {"tavily", "qveris"})

        for workflow in ("macro", "quant", "general"):
            with self.subTest(workflow=workflow):
                self.assertEqual(load_mcp_specs(config, workflow=workflow), {})

    def test_registered_tool_allowlists_match_canonical_server_tools(self) -> None:
        base = AppConfig.from_env(ROOT)
        config = dataclasses.replace(base, mcp_config_path=ROOT / ".mcp.example.json")
        specs = load_mcp_specs(config, workflow="industry")

        self.assertEqual(
            specs["tavily"]["allowedTools"],
            [
                "tavily_search",
                "tavily_extract",
                "tavily_crawl",
                "tavily_map",
                "tavily_research",
            ],
        )
        self.assertEqual(
            specs["qveris"]["allowedTools"],
            ["discover", "inspect", "usage_history", "credits_ledger"],
        )

    def test_mcp_env_references_resolve_without_embedding_secrets(self) -> None:
        with patch.dict(os.environ, {"TEST_MCP_KEY": "secret-value"}, clear=False):
            resolved = resolve_mcp_env(
                {
                    "API_KEY": "${TEST_MCP_KEY}",
                    "MISSING": "${NOT_SET_FOR_TEST}",
                    "RETRIES": "${NOT_SET_FOR_TEST:-3}",
                    "LITERAL": "stdio",
                }
            )

        self.assertEqual(resolved["API_KEY"], "secret-value")
        self.assertNotIn("MISSING", resolved)
        self.assertEqual(resolved["RETRIES"], "3")
        self.assertEqual(resolved["LITERAL"], "stdio")


if __name__ == "__main__":
    unittest.main()
