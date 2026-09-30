from __future__ import annotations

import json
import re

from openai import AsyncOpenAI

from .config import AppConfig
from .context import ConversationContext
from .db import Storage
from .memory import MemoryStore
from .harness.contracts import RunRequest, Scope
from .harness.runtime import RunSession


def build_model_client(config: AppConfig) -> AsyncOpenAI | None:
    """Build the OpenAI-compatible client for the configured domestic model.

    Returns ``None`` when no domestic endpoint is configured; the core adapter
    rejects incomplete configuration instead of selecting a default endpoint. The client bypasses environment
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

    async def run_result(self, context: ConversationContext, message: str, role: str = "user", *,
                         as_of_date=None, workflow=None, planning_constraints=None):
        """Chat owns no facts or official authority; all normal work enters the core."""
        dates = set(re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", message))
        for value in re.findall(r"(?<!\d)20\d{6}(?!\d)", message):
            dates.add(f"{value[:4]}-{value[4:6]}-{value[6:]}")
        if as_of_date is None:
            as_of_date = next(iter(dates)) if len(dates) == 1 else None
        try:
            request = RunRequest(Scope.from_context(self.config.root_dir, context), message,
                                 as_of_date=as_of_date, workflow=workflow, host=context.platform)
        except ValueError:
            # A malformed date still reaches the core's explicit-date gate and trace.
            request = RunRequest(Scope.from_context(self.config.root_dir, context), message,
                                 workflow=workflow, host=context.platform)
        self.storage.add_message(context.conversation_id, role, message)
        if self.config.fake_ai:
            trace=RunSession(self.storage,request)
            trace.step("model_adapter", {"mode":"fake","network":False})
            outcome=trace.finish(f"[fake-ai] received: {message}\nrun_id={trace.run_id}")
        else:
            from .harness.engine import Harness
            from .sdk_research import SDKResearchAdapter
            adapter=SDKResearchAdapter(self.config)
            outcome=await Harness(self.config.root_dir,self.storage,self.config.data_dir/"harness_runs",
                                  self.config.market_timezone).run_async(request,
                framework_adapter=adapter,research_adapter=adapter,planning_constraints=planning_constraints)
        self.storage.add_message(context.conversation_id,"assistant",outcome.output)
        return outcome

    def run_core_result(self, context: ConversationContext, message: str, *, as_of_date: str,
                        packet: dict, workflow: str, research_spec: dict | None = None, outlook_spec: dict | None = None, mixed_spec: dict | None = None, mode="research", compile_framework=False, planning_constraints=None):
        """Synchronous trusted host entry; reviewed facts never come from chat prose."""
        from .harness.engine import Harness
        from .sdk_research import SDKResearchAdapter
        if workflow not in {"company", "industry", "outlook", "mixed"}:
            raise ValueError("Configured research execution requires company/industry/outlook/mixed")
        request = RunRequest(Scope.from_context(self.config.root_dir, context), message,
                             as_of_date, mode, workflow, host=context.platform)
        engine = Harness(self.config.root_dir, self.storage, self.config.data_dir / "harness_runs",
                         self.config.market_timezone)
        # The core owns the single run and all budgets, evaluation and publication.
        adapter=SDKResearchAdapter(self.config)
        return engine.run(request, packet, research_spec=research_spec,
                          research_adapter=adapter, outlook_spec=outlook_spec, mixed_spec=mixed_spec,
                          framework_adapter=adapter if compile_framework else None,
                          planning_constraints=planning_constraints)

    async def run_core_result_async(self, context: ConversationContext, message: str, *, as_of_date,
                                    packet=None, workflow=None, mode="research", compile_framework=True,
                                    planning_constraints=None, research_spec=None, quant_spec=None,
                                    outlook_spec=None, mixed_spec=None, wall_clock_seconds=120):
        """Trusted asynchronous host; cancellation drains the same core run before returning."""
        from .harness.engine import Harness
        from .sdk_research import SDKResearchAdapter
        request=RunRequest(Scope.from_context(self.config.root_dir,context),message,as_of_date,mode,workflow,
                           wall_clock_seconds=wall_clock_seconds,host=context.platform)
        adapter=SDKResearchAdapter(self.config)
        return await Harness(self.config.root_dir,self.storage,self.config.data_dir/"harness_runs",self.config.market_timezone).run_async(
            request,packet,research_spec=research_spec,quant_spec=quant_spec,outlook_spec=outlook_spec,mixed_spec=mixed_spec,
            research_adapter=adapter,framework_adapter=adapter if compile_framework else None,
            planning_constraints=planning_constraints)

    def plan_core_result(self, context: ConversationContext, message: str, *, as_of_date=None, workflow=None, planning_constraints=None):
        """Tool-free framework proposal and independent review; never acquisition."""
        from .harness.engine import Harness
        from .sdk_research import SDKResearchAdapter
        request=RunRequest(Scope.from_context(self.config.root_dir,context),message,as_of_date,"plan",workflow,host=context.platform)
        return Harness(self.config.root_dir,self.storage,self.config.data_dir/"harness_runs",self.config.market_timezone).run(
            request,framework_adapter=SDKResearchAdapter(self.config),planning_constraints=planning_constraints)

    def read_core_report(self, context: ConversationContext, run_id: str, *, as_of_date=None, format="markdown"):
        """Trusted host reads through the same authorization/integrity gate as CLI."""
        from .harness.delivery import read_report
        from .harness.trace import TraceRepository
        if format not in {"markdown", "json"}:
            raise ValueError("Unknown report format")
        delivery = read_report(TraceRepository(self.storage), Scope.from_context(self.config.root_dir, context),
                               run_id, self.config.data_dir / "harness_runs", as_of_date=as_of_date)
        return delivery["markdown"] if format == "markdown" else {k:v for k,v in delivery.items() if k != "markdown"}

    def _optional_codex_tools(self) -> list[object]:
        """Legacy flags cannot reopen unrestricted execution."""
        return []
