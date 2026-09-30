"""A run is created before host/model work; execution status and policy decisions differ."""
from __future__ import annotations

import asyncio
import time
from threading import Event, RLock

from .contracts import EvalResult, FailureCategory, RunOutcome, RunRequest, RunStatus, ToolResult, canonical, digest, redact
from .trace import TraceRepository, now


class RunControl:
    """Host cancellation and official commit share one linearization lock."""
    def __init__(self):
        self.lock = RLock()
        self.cancelled = Event()

    def cancel(self):
        with self.lock:
            self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise asyncio.CancelledError()


class RunSession:
    def __init__(self, storage, request: RunRequest, control=None):
        self.control = control or RunControl()
        self.repository = TraceRepository(storage)
        self.request = request
        self.run_id = self.repository.begin(request)
        self.started = time.monotonic()
        self.tool_count = 0
        self.failure = None
        self.closed = False
        self.step("request", {"host": request.host, "mode": request.mode, "as_of_date": request.as_of_date})

    @property
    def remaining(self):
        return max(0, self.request.wall_clock_seconds - (time.monotonic() - self.started))

    def checkpoint(self):
        self.control.check()
        if self.remaining <= 0:
            raise TimeoutError("budget_exceeded")

    def step(self, stage, detail, status="ok"):
        return self.repository.step(self.run_id, self.request.scope, stage, detail, status)

    def evaluate(self, result):
        self.repository.evaluate(self.run_id, self.request.scope, result)

    async def tool(self, name, arguments, callback, allowed):
        started = time.monotonic()
        permission = "allow"
        self.tool_count += 1
        if name not in allowed:
            permission = "expected_denial"
            result = ToolResult(False, "denied", "expected_denial")
            self.evaluate(EvalResult("tool_permission", True, code="expected_denial", evidence={"tool": name}))
        elif self.tool_count > self.request.max_tool_calls or self.remaining <= 0:
            result = ToolResult(False, "error", "budget_exceeded")
            self.failure = FailureCategory.TIMEOUT_OR_BUDGET_FAILURE
        else:
            try:
                obj = await asyncio.wait_for(callback(), timeout=self.remaining)
                result = obj if isinstance(obj, ToolResult) else ToolResult.from_legacy(obj)
            except TimeoutError:
                result = ToolResult(False, "error", "timeout", retryable=True)
                self.failure = FailureCategory.TIMEOUT_OR_BUDGET_FAILURE
            except (ValueError, TypeError, KeyError):
                result = ToolResult(False, "error", "invalid_schema")
                self.failure = FailureCategory.TOOL_RETURN_FAILURE
            except Exception:
                result = ToolResult(False, "error", "tool_error")
                self.failure = FailureCategory.RETRIEVAL_INTERFACE_FAILURE
        envelope = result.json()
        envelope["usage"] = {**result.usage, "latency_ms": round((time.monotonic() - started) * 1000, 3),
                              "output_chars": len(canonical(result.data))}
        self.repository.append("tool_calls", self.run_id, self.request.scope, tool_name=name,
            arguments_hash=digest(redact(arguments)), permission=permission,
            envelope_json=canonical(redact(envelope)), recorded_at=now())
        return envelope

    def finish(self, output, status=RunStatus.SUCCEEDED, *, action="NO_ACTION", official=False, state=None):
        with self.control.lock:
            if status == RunStatus.SUCCEEDED and self.failure is None:
                self.checkpoint()
            return self._finish(output, status, action=action, official=official, state=state)

    def _finish(self, output, status, *, action, official, state):
        if self.closed:
            raise ValueError("Run already closed")
        if self.failure is not None:
            status, official, state, action = RunStatus.FAILED, False, None, "NO_ACTION"
        if status != RunStatus.SUCCEEDED and (official or action != "NO_ACTION"):
            raise ValueError("Failed/blocked runs cannot publish actions")
        self.step("final_output", {"output_hash": digest(output), "output_chars": len(output),
                                   "action": action, "official_output_allowed": official}, status.value)
        outcome = RunOutcome(self.run_id, status, output, action, official, self.failure or getattr(self, "attribution", None))
        self.repository.finish(outcome, self.request.scope, state, self.request.as_of_date)
        self.closed = True
        return outcome
