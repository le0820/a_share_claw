from __future__ import annotations

import json
import asyncio
import re
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
from .harness.contracts import RunRequest, RunStatus, Scope, FailureCategory, EvalResult, canonical, digest
from .harness.runtime import RunSession


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
        return (await self.run_result(context, message, role)).output

    async def run_result(self, context: ConversationContext, message: str, role: str = "user"):
        dates = set(re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", message))
        for value in re.findall(r"(?<!\d)20\d{6}(?!\d)", message):
            dates.add(f"{value[:4]}-{value[4:6]}-{value[6:]}")
        # Date validity belongs to the run gate, so malformed dates still have a trace.
        try:
            as_of = next(iter(dates)) if len(dates) == 1 else None
            request = RunRequest(Scope.from_context(self.config.root_dir, context), message,
                                 as_of_date=as_of, host=context.platform)
        except ValueError:
            request = RunRequest(Scope.from_context(self.config.root_dir, context), message, host=context.platform)
        trace = RunSession(self.storage, request)
        try:
            self.storage.add_message(context.conversation_id, role, message)
            if self.config.fake_ai:
                trace.step("model_adapter", {"mode": "fake", "network": False})
                outcome = trace.finish(f"[fake-ai] received: {message}\nrun_id={trace.run_id}")
            else:
                output = await asyncio.wait_for(self._run_agents_sdk(context, message, trace=trace), timeout=trace.remaining)
                outcome = trace.finish(output, getattr(trace, "result_status", RunStatus.BLOCKED))
        except asyncio.CancelledError:
            trace.finish(f"Run cancelled; run_id={trace.run_id}", RunStatus.CANCELLED)
            raise
        except TimeoutError:
            trace.failure = FailureCategory.TIMEOUT_OR_BUDGET_FAILURE
            outcome = trace.finish(f"Run timed out; NO_ACTION; run_id={trace.run_id}", RunStatus.FAILED)
        except Exception as exc:
            trace.failure = FailureCategory.SYNTHESIS_OR_UNKNOWN_FAILURE
            trace.step("exception", {"type": type(exc).__name__}, "error")
            outcome = trace.finish(f"Agent run failed; NO_ACTION; run_id={trace.run_id}", RunStatus.FAILED)
        self.storage.add_message(context.conversation_id, "assistant", outcome.output)
        return outcome

    def run_core_result(self, context: ConversationContext, message: str, *, as_of_date: str,
                        packet: dict, research_spec: dict, workflow: str, mode="research"):
        """Synchronous trusted host entry; reviewed facts never come from chat prose."""
        from .harness.engine import Harness
        from .sdk_research import SDKResearchAdapter
        if workflow not in {"company", "industry"}:
            raise ValueError("Configured research execution requires company/industry")
        request = RunRequest(Scope.from_context(self.config.root_dir, context), message,
                             as_of_date, mode, workflow, host=context.platform)
        engine = Harness(self.config.root_dir, self.storage, self.config.data_dir / "harness_runs",
                         self.config.market_timezone)
        # The core owns the single run and all budgets, evaluation and publication.
        return engine.run(request, packet, research_spec=research_spec,
                          research_adapter=SDKResearchAdapter(self.config))

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

    async def _run_agents_sdk(self, context: ConversationContext, message: str, *, trace=None) -> str:
        owned = trace is None
        if owned:
            trace = RunSession(self.storage, RunRequest(Scope.from_context(self.config.root_dir, context), message, host=context.platform))
        try:
            output = await self._execute_sdk(context, message, trace)
            if owned:
                trace.finish(output, trace.result_status)
            return output
        except BaseException as exc:
            if owned:
                trace.failure = None if isinstance(exc, asyncio.CancelledError) else FailureCategory.SYNTHESIS_OR_UNKNOWN_FAILURE
                trace.finish("SDK run did not complete", RunStatus.CANCELLED if isinstance(exc, asyncio.CancelledError) else RunStatus.FAILED)
            raise

    async def _execute_sdk(self, context, message, trace):
        try:
            from agents import Agent, Runner, function_tool
        except ImportError as exc:
            raise RuntimeError("Install dependencies first: pip install -e .") from exc
        from .data_plugins import DataRun, default_registry, preview
        from .sdk_trace import TraceHooks

        research_runtime = ResearchRuntime(self.config, context)
        research_context = build_research_context(self.config, context, message, include_state=False)
        data_run = DataRun(default_registry().snapshot(), self.config.data_dir / "plugin_runs",
                           canonical(trace.request.scope.__dict__), run_id=trace.run_id)
        allowed = tool_names_for_workflow(research_context.workflow)
        trace.step("route", {"workflow": research_context.workflow.value, "reason": research_context.routing_reason})
        trace.step("context", {"loaded_files": research_context.loaded_files, "missing_files": research_context.missing_files,
                               "state_scope": research_context.state_scope, "instructions_hash": digest(research_context.instructions)})
        if research_context.missing_files:
            trace.result_status = RunStatus.BLOCKED
            trace.attribution = FailureCategory.CONTEXT_TRUNCATION_FAILURE
            trace.evaluate(EvalResult("required_context", False,
                           category=FailureCategory.CONTEXT_TRUNCATION_FAILURE, code="required_context_missing"))
            return "NEED_EVIDENCE; NO_ACTION; required_context_missing; run_id=" + trace.run_id
        self._configure_model_client()

        async def invoke(name, arguments, callback):
            return await trace.tool(name, arguments, callback, allowed)

        @function_tool
        async def list_data_plugins() -> str:
            """List pinned source plugins, supported capabilities and credential readiness; no network."""
            async def call():
                return {"ok": True, "data": data_run.list_providers()}
            return json_dumps(await invoke("list_data_plugins", {}, call))

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
            async def call():
                try:
                    plan = json.loads(plan_json)
                    if trace.request.as_of_date and any(r.get("as_of_date", "") > trace.request.as_of_date for r in plan.get("requirements", [])):
                        return {"ok": False, "error_code": "future_data"}
                    result = data_run.plan(plan)
                    trace.step("data_plan", {"requirements": [r.json() for r in data_run.requirements.values()], "framework_hash": digest(plan["framework"])})
                    return {"ok": True, "data": result}
                except (ValueError, TypeError, AttributeError):
                    return {"ok": False, "error_code": "invalid_plan"}
            return json_dumps(await invoke("plan_data", {"plan_hash": digest(plan_json)}, call))

        @function_tool
        async def fetch_data(requirement_id: str) -> str:
            """Fetch a declared gap through its pinned plugin; never accepts arbitrary tools or commands."""
            async def call():
                result = await data_run.fetch(requirement_id)
                p = result.get("provenance", {})
                if p:
                    from .harness.trace import now
                    trace.repository.append("artifacts", trace.run_id, trace.request.scope,
                        detail_json=canonical({**p, "directory": str(data_run.directory), "mode": "research"}), recorded_at=now())
                return result
            full = await invoke("fetch_data", {"requirement_id": requirement_id}, call)
            rendered = preview(full, self.config.max_tool_output_chars)
            trace.step("tool_preview", {"tool": "fetch_data", "truncated": json.loads(rendered)["truncated"], "output_chars": len(rendered)})
            return rendered

        @function_tool
        async def data_gap_report() -> str:
            """Show missing/unverified requirements and the research-only output gate."""
            async def call():
                return {"ok": True, "data": data_run.summary()}
            return json_dumps(await invoke("data_gap_report", {}, call))

        @function_tool
        async def get_compiled_rule(layer: str, section: str | None = None) -> str:
            """Read immutable local scoring policy, not observations or external data."""
            async def call():
                return json.loads(await research_runtime.get_compiled_rule(layer, section))
            return json_dumps(await invoke("get_compiled_rule", {"layer": layer, "section": section}, call))

        @function_tool
        async def get_market_session_status(as_of_date: str) -> str:
            """Compute the A-share market-time gate from the host clock; does not fetch market data."""
            async def call():
                return json.loads(await research_runtime.get_market_session_status(as_of_date))
            return json_dumps(await invoke("get_market_session_status", {"as_of_date": as_of_date}, call))

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
        trace.step("model_adapter", {"provider": self.config.model_provider, "model": self._effective_model()})
        result = await Runner.run(agent, message, hooks=TraceHooks(trace, self.config.model_provider, self._effective_model(), self.config.model_base_url))
        summary = data_run.summary()
        trace.step("model_final", {"output_hash": digest(str(result.final_output)), "output_chars": len(str(result.final_output))})
        data_run.directory.mkdir(parents=True, exist_ok=True)
        data_run._save("summary.json", summary)
        # Completing a model-declared acquisition plan does not complete the
        # user's workflow. Core normalization and business evaluation are pending.
        trace.result_status = RunStatus.BLOCKED
        trace.step("business_gate", {"status": "core_evaluation_pending"})
        trace.evaluate(EvalResult("agent_official_output", True, code="research_only_no_action"))
        trace.step("gap_report", summary)
        # The model's final prose is not an evaluated action. The host owns delivery.
        return "[数据边界] 研究取证；正式评分与行动须由核心门禁执行。NO_ACTION; run_id=" + trace.run_id + "; " + json_dumps(summary)

    def _optional_codex_tools(self) -> list[object]:
        """Legacy configuration cannot reopen unrestricted network/file execution."""
        return []
