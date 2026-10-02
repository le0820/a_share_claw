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
        self.active_phase = None
        self.phase_sequence = 0
        self.phase_started = None
        self.last_phase = None
        self.phase_events = []
        self.action_lock = RLock()
        self.open_actions = {}
        self.step("request", {"host": request.host, "mode": request.mode, "as_of_date": request.as_of_date})

    @property
    def remaining(self):
        return max(0, self.request.wall_clock_seconds - (time.monotonic() - self.started))

    def checkpoint(self):
        self.control.check()
        if self.remaining <= 0:
            raise TimeoutError("budget_exceeded")

    def step(self, stage, detail, status="ok"):
        result = self.repository.step(self.run_id, self.request.scope, stage, detail, status)
        if self.active_phase is not None and stage != "react_phase":
            self.phase_events.append({"stage": stage, "status": status, "detail_hash": digest(redact(detail))})
        return result

    def phase(self, name, decision_code, inputs=None):
        """Code-owned ReAct boundaries; summaries and hashes, never private reasoning.

        A phase ends before its successor starts. Failure/cancellation closes the
        active phase at termination, so missing observations cannot look successful.
        """
        successors = {None: {"context"}, "context": {"planning"},
                      "planning": {"evidence", "publish"}, "evidence": {"compute"},
                      "compute": {"output"}, "output": {"publish"}, "publish": set()}
        previous = self.active_phase or self.last_phase
        if name not in successors[previous]:
            raise ValueError("invalid_execution_transition")
        self.end_phase("ok")
        self.phase_sequence += 1
        self.active_phase, self.phase_started = name, time.monotonic()
        self.phase_events = []
        self.step("react_phase", {"schema_version": "react-boundary-v1", "phase": name,
                  "sequence": self.phase_sequence, "boundary": "start",
                  "decision_code": decision_code, "input_hash": digest(inputs or {}),
                  "reasoning_kind": "public_decision_summary"}, "running")

    def end_phase(self, status, observation=None):
        if self.active_phase is None:
            return
        self.step("react_phase", {"schema_version": "react-boundary-v1", "phase": self.active_phase,
                  "sequence": self.phase_sequence, "boundary": "end",
                  "duration_ms": round((time.monotonic()-self.phase_started)*1000, 3),
                  "observation": observation or {}, "event_count": len(self.phase_events),
                  "events_hash": digest(self.phase_events)}, status)
        self.last_phase, self.active_phase = self.active_phase, None

    def action(self, kind, operation, decision_code, inputs=None):
        from .observability import ActionSpan
        return ActionSpan(self, kind, operation, decision_code, inputs or {})

    def evaluate(self, result):
        with self.action("evaluation", result.evaluator, "apply_core_gate", {"evaluation_hash": digest(result.json())}) as span:
            self.repository.evaluate(self.run_id, self.request.scope, result)
            span.observe(status="ok" if result.passed else "rejected", passed=result.passed, hard_gate=result.hard_gate, error_code=result.code)

    async def tool(self, name, arguments, callback, allowed):
        with self.action("role", name, "check_permission_and_execute_bounded_callback", arguments) as span:
            result = await self._tool(name, arguments, callback, allowed)
            span.observe(status="ok" if result["ok"] else "denied" if result["status"] == "denied" else "error",
                         output_hash=digest(result), error_code=result["error_code"])
            return result

    async def _tool(self, name, arguments, callback, allowed):
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

    def source_tool(self,name,arguments,callback,payload):
        with self.action("source", name, "fetch_only_frozen_requirement", arguments) as span:
            result = self._source_tool(name,arguments,callback,payload)
            span.observe(status="ok" if result["ok"] else "error", output_hash=digest(result),
                         error_code=result["error_code"], fallback_status=result["fallback_status"], truncated=result["truncated"])
            return result

    def _source_tool(self,name,arguments,callback,payload):
        """Host-only source callback; trace hashes facts instead of copying native rows."""
        from .research import bounded_call
        self.checkpoint();self.tool_count+=1
        if self.tool_count>self.request.max_tool_calls:raise TimeoutError("budget_exceeded")
        started=time.monotonic()
        try:
            obj=bounded_call(callback,payload,self.remaining,self.control)
            result=obj if isinstance(obj,ToolResult) else ToolResult.from_legacy(obj)
        except TimeoutError:raise
        except (ValueError,TypeError,KeyError):result=ToolResult(False,"error","invalid_schema")
        except Exception:result=ToolResult(False,"error","provider_error",retryable=True)
        envelope=result.json();self.checkpoint()
        envelope["usage"]={**envelope["usage"],"latency_ms":round((time.monotonic()-started)*1000,3),"output_chars":len(canonical(result.data))}
        traced={**envelope,"data":{"sha256":digest(result.data),"chars":len(canonical(result.data))}}
        self.repository.append("tool_calls",self.run_id,self.request.scope,tool_name=name,arguments_hash=digest(redact(arguments)),
            permission="trusted_host_source",envelope_json=canonical(redact(traced)),recorded_at=now())
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
        terminal_boundary = None
        if self.active_phase is not None:
            terminal_boundary = {"schema_version": "react-boundary-v1", "phase": self.active_phase,
                "sequence": self.phase_sequence, "boundary": "end",
                "duration_ms": round((time.monotonic()-self.phase_started)*1000, 3),
                "event_count": len(self.phase_events), "events_hash": digest(self.phase_events),
                "observation": {"output_hash": digest(output), "action": action, "official_output_allowed": official}}
        with self.action_lock:
            interrupted = [span.end_detail("interrupted") for span in reversed(list(self.open_actions.values()))]
            self.repository.finish(outcome, self.request.scope, state, self.request.as_of_date,
                                   terminal_boundary=terminal_boundary, interrupted_actions=interrupted)
            for span in self.open_actions.values():
                span.ended = True
            self.open_actions.clear()
            self.closed = True
        if self.active_phase is not None:
            self.last_phase, self.active_phase = self.active_phase, None
        self.closed = True
        return outcome
