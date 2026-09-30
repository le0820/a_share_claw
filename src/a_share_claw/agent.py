from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI

from .config import AppConfig
from .context import ConversationContext
from .db import Storage
from .memory import MemoryStore
from .prompts import build_instructions
from .research_context import (
    build_research_context,
    tool_names_for_workflow,
)
from .research_tools import ResearchRuntime
from .utils import json_dumps


def build_model_client(config: AppConfig) -> AsyncOpenAI | None:
    """Build the OpenAI-compatible client for the configured domestic model.

    Returns ``None`` when no domestic endpoint is configured, so the agent falls
    back to the ``openai_model`` placeholder. The client bypasses environment
    proxies by default, since domestic endpoints (e.g. tokenhub) should connect
    directly; set ``ASCLAW_MODEL_TRUST_ENV=1`` to route through a proxy.
    """
    if not (config.model_base_url and config.model_api_key):
        return None
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("Install dependencies first: pip install -e .") from exc
    http_client = httpx.AsyncClient(trust_env=config.model_trust_env)
    return AsyncOpenAI(
        base_url=config.model_base_url,
        api_key=config.model_api_key,
        http_client=http_client,
    )


class InvestmentAgent:
    def __init__(self, config: AppConfig, storage: Storage):
        self.config = config
        self.storage = storage
        self.memory = MemoryStore(storage, config.data_dir)

    async def run(self, context: ConversationContext, message: str, role: str = "user") -> str:
        self.storage.add_message(context.conversation_id, role, message)
        if self.config.fake_ai:
            output = f"[fake-ai] received: {message}"
            self.storage.add_message(context.conversation_id, "assistant", output)
            return output
        try:
            output = await self._run_agents_sdk(context, message)
        except Exception as exc:
            output = f"Agent run failed: {exc}"
        self.storage.add_message(context.conversation_id, "assistant", output)
        return output

    def _effective_model(self) -> str:
        # `model_name` is the configured domestic model; `openai_model` stays as the
        # tutorial's placeholder/fake value when no domestic model is set.
        return self.config.model_name or self.config.openai_model

    def _configure_model_client(self) -> None:
        client = build_model_client(self.config)
        if client is None:
            return
        try:
            from agents import set_default_openai_client, set_tracing_disabled
        except ImportError as exc:
            raise RuntimeError("Install dependencies first: pip install -e .") from exc
        # Disable tracing without building the default tracing exporter. The SDK
        # lazily builds a BackendSpanExporter backed by an httpx.Client that
        # inherits the environment proxy; on a SOCKS proxy without socksio that
        # crashes at construction ("Using SOCKS proxy..."). Installing a provider
        # with no processor avoids building the exporter, and our domestic model
        # endpoint connects directly (trust_env=False) anyway.
        try:
            from agents.tracing import set_trace_provider
            from agents.tracing.provider import DefaultTraceProvider

            set_trace_provider(DefaultTraceProvider())
        except Exception:
            pass
        set_tracing_disabled(True)
        set_default_openai_client(client, use_for_tracing=False)

    async def _run_agents_sdk(self, context: ConversationContext, message: str) -> str:
        try:
            from agents import Agent, Runner, function_tool
        except ImportError as exc:
            raise RuntimeError("Install dependencies first: pip install -e .") from exc
        from .data_plugins import DataRun, default_registry, preview

        self._configure_model_client()
        research_runtime = ResearchRuntime(self.config, context)
        research_context = build_research_context(self.config, context, message, include_state=False)
        data_run = DataRun(default_registry().snapshot(), self.config.data_dir / "plugin_runs",
                           json.dumps([str(self.config.root_dir.resolve()), context.platform,
                                       context.user_id, context.session_id, context.agent_key]))

        @function_tool
        async def list_data_plugins() -> str:
            """List pinned source plugins, supported capabilities and credential readiness; no network."""
            return json_dumps(data_run.list_providers())

        @function_tool
        async def plan_data(plan_json: str) -> str:
            """Before fetching, declare framework and requirements as JSON. Each requirement has
            requirement_id, provider, capability, as_of_date (YYYY-MM-DD), params and required.
            Extend a plan by including its unchanged existing requirements plus new ones.
            Parameters: fred macro.series: series_id/start_date/end_date;
            sec company.facts: cik/concepts (e.g. us-gaap:Assets)/start_date;
            nbs/pbc macro.release: url; macro.release_index: optional url (discovery only);
            tickflow market.quote: symbol; market.daily_bars: symbol/start_date/end_date/adjust;
            tickflow financial.income/financial.balance_sheet/financial.cash_flow: symbols/start_date/end_date.
            """
            try:
                return json_dumps(data_run.plan(json.loads(plan_json)))
            except (ValueError, TypeError):
                return json_dumps({"ok": False, "error_code": "invalid_plan",
                                   "message": "Use the declared plan schema and unique requirement IDs"})

        @function_tool
        async def fetch_data(requirement_id: str) -> str:
            """Fetch a declared gap through its pinned plugin; never accepts arbitrary tools or commands."""
            return preview(await data_run.fetch(requirement_id), self.config.max_tool_output_chars)

        @function_tool
        async def data_gap_report() -> str:
            """Show missing/unverified requirements and the research-only output gate."""
            return json_dumps(data_run.summary())

        @function_tool
        async def get_compiled_rule(layer: str, section: str | None = None) -> str:
            """Read immutable local scoring policy, not observations or external data."""
            return await research_runtime.get_compiled_rule(layer, section)

        @function_tool
        async def get_market_session_status(as_of_date: str) -> str:
            """Compute the A-share market-time gate from the host clock; does not fetch market data."""
            return await research_runtime.get_market_session_status(as_of_date)

        available_tools = {tool.name: tool for tool in (
            list_data_plugins, plan_data, fetch_data, data_gap_report,
            get_compiled_rule, get_market_session_status,
        )}
        enabled_names = tool_names_for_workflow(research_context.workflow)
        tools = [tool for name, tool in available_tools.items() if name in enabled_names]
        today = datetime.now(ZoneInfo(self.config.timezone)).date().isoformat()
        market_clock = datetime.now(ZoneInfo(self.config.market_timezone))
        instructions = build_instructions(
            current_date=today, timezone=self.config.timezone,
            market_date=market_clock.date().isoformat(), market_timezone=self.config.market_timezone,
            market_now=market_clock.isoformat(), long_memory="", asset_snippets="",
            research_context=research_context.instructions, workflow=research_context.workflow.value,
        )
        agent = Agent(name="a_share_claw", model=self._effective_model(), instructions=instructions,
                      tools=tools, mcp_servers=[])
        # Old SDK sessions can contain arbitrary web/file/MCP tool results. They are
        # intentionally not replayed into the plugin-only evidence boundary.
        result = await Runner.run(agent, message)
        summary = data_run.summary()
        output = str(result.final_output)
        if data_run.requirements is not None:
            data_run._save("summary.json", summary)
        return output + "\n\n[数据边界] 研究草稿，未经正式评分与事实评估。run_id=" + data_run.run_id + "; " + json_dumps(summary)

    def _optional_codex_tools(self) -> list[object]:
        """Legacy configuration cannot reopen unrestricted network/file execution."""
        return []
