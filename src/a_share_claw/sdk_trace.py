"""Existing SDK adapter hooks; model SDK dependencies stay outside Harness core."""
from __future__ import annotations

import json
import time
from threading import Lock

from agents import RunHooks

from .harness.contracts import PromptCacheTrace, canonical, digest, redact
from .harness.trace import now


_provider_lock = Lock()
_local_trace_provider = None


def disable_remote_tracing():
    """Use only local Harness hooks; avoid constructing the SDK's remote exporter."""
    from agents import set_tracing_disabled
    from agents.tracing import set_trace_provider
    from agents.tracing.provider import DefaultTraceProvider
    global _local_trace_provider
    with _provider_lock:
        if _local_trace_provider is None:
            _local_trace_provider = DefaultTraceProvider()
        set_trace_provider(_local_trace_provider)
        set_tracing_disabled(True)


class TraceHooks(RunHooks):
    def __init__(self, session, provider, model, endpoint=None, *, operation=None):
        self.session, self.provider, self.model = session, provider, model
        self.endpoint = endpoint
        self.operation = operation
        self.call_id = None
        self.call_index = 0
        self.previous_input = None

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self.started = time.monotonic()
        serialized = json.dumps(input_items, sort_keys=True, default=lambda v: v.model_dump() if hasattr(v, "model_dump") else type(v).__name__)
        tools = [{"name": tool.name, "schema": getattr(tool, "params_json_schema", {})} for tool in sorted(agent.tools, key=lambda t: t.name)]
        cache = PromptCacheTrace(self.provider, self.model, digest(system_prompt or ""), digest(tools),
                                 digest({"system": system_prompt, "tools": tools}), digest(serialized))
        raw = serialized.encode()
        common = None
        if self.previous_input is not None:
            common = 0
            for left, right in zip(self.previous_input, raw):
                if left != right: break
                common += 1
        self.call_index += 1
        self.detail = {**cache.json(), "endpoint": redact(self.endpoint), "operation": self.operation, "input_hash": digest(serialized),
                       "input_chars": len(serialized), "call_index": self.call_index, "previous_call_id": self.call_id,
                       "serialization_version": "SDK-input-canonical-v1", "token_count_method": "SDK-total; sections unavailable",
                       "common_input_prefix_bytes": common, "pricing_snapshot_version": None,
                       "usage_source": "SDK_normalized; raw cache availability unknown", "cache_supported": None}
        self.previous_input = raw
        self.call_id = self.session.repository.append("model_calls", self.session.run_id, self.session.request.scope,
            status="running", detail_json=canonical(redact(self.detail)), started_at=now())

    async def on_llm_end(self, context, agent, response):
        usage = response.usage
        detail = {**self.detail, "request_latency_ms": round((time.monotonic() - self.started) * 1000, 3),
                  "input_tokens": getattr(usage, "input_tokens", None),
                  "output_tokens": getattr(usage, "output_tokens", None),
                  "response_id_hash": digest(getattr(response, "response_id", None))}
        self.session.repository.model_end(self.session.run_id, self.session.request.scope, self.call_id, detail)
