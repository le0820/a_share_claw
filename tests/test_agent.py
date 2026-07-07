from __future__ import annotations

import dataclasses
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.config import AppConfig
from a_share_claw.agent import build_model_client, InvestmentAgent


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
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7890"}):
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


if __name__ == "__main__":
    unittest.main()
