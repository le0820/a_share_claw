"""E0 contracts shared by hosts, evaluators and future data adapters (stdlib only)."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class FailureCategory(str, Enum):
    RETRIEVAL_INTERFACE_FAILURE = "RETRIEVAL_INTERFACE_FAILURE"
    TOOL_RETURN_FAILURE = "TOOL_RETURN_FAILURE"
    CONTEXT_TRUNCATION_FAILURE = "CONTEXT_TRUNCATION_FAILURE"
    STATE_CONTAMINATION_FAILURE = "STATE_CONTAMINATION_FAILURE"
    PERMISSION_POLICY_FAILURE = "PERMISSION_POLICY_FAILURE"
    TIMEOUT_OR_BUDGET_FAILURE = "TIMEOUT_OR_BUDGET_FAILURE"
    SYNTHESIS_OR_UNKNOWN_FAILURE = "SYNTHESIS_OR_UNKNOWN_FAILURE"


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use YYYY-MM-DD")
    return date.fromisoformat(value).isoformat()


def redact(value: Any) -> Any:
    """Trace policy: no credentials/query strings; free-text input is recorded by hash."""
    if isinstance(value, dict):
        return {str(k): "[redacted]" if re.search(r"api.?key|authorization|password|secret|credential|user.agent|email", str(k), re.I)
                else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        if value.startswith(("https://", "http://")):
            try:
                p = urlsplit(value)
                return urlunsplit((p.scheme, p.hostname or "", p.path, "", ""))
            except ValueError:
                return "[invalid URL]"
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", value)
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted email]", value)
        value = re.sub(r"(?i)(api[_-]?key|token|password|secret)([=:]\s*)[^\s&\"']+", r"\1\2[redacted]", value)
    return value


@dataclass(frozen=True)
class Scope:
    workspace: str
    principal: str
    session: str
    agent_key: str = "default"

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in asdict(self).values()):
            raise ValueError("All scope fields are required")

    @property
    def key(self) -> str:
        return digest(asdict(self))

    @classmethod
    def from_context(cls, root, context):
        return cls(str(root.resolve()), f"{context.platform}:{context.platform_user_id}",
                   f"{context.chat_id}:{context.session_id}", context.agent_key)


@dataclass(frozen=True)
class RunRequest:
    scope: Scope
    message: str
    as_of_date: str | None = None
    mode: str = "research"
    workflow: str | None = None
    wall_clock_seconds: float = 120
    max_tool_calls: int = 50
    host: str = "library"

    def __post_init__(self):
        if self.mode not in {"plan", "research", "replay", "official"}:
            raise ValueError("Unknown output mode")
        if self.as_of_date is not None:
            validate_date(self.as_of_date)
        if self.mode in {"official", "replay"} and self.as_of_date is None:
            raise ValueError("Official/replay requests require an explicit date")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Request message is required")
        if (isinstance(self.wall_clock_seconds, bool) or not isinstance(self.wall_clock_seconds, (int, float)) or
                not math.isfinite(self.wall_clock_seconds) or self.wall_clock_seconds <= 0 or
                isinstance(self.max_tool_calls, bool) or not isinstance(self.max_tool_calls, int) or self.max_tool_calls < 1):
            raise ValueError("Budgets must be positive")


@dataclass(frozen=True)
class EvalResult:
    evaluator: str
    passed: bool
    hard_gate: bool = True
    category: FailureCategory | None = None
    code: str | None = None
    evidence: dict = field(default_factory=dict)
    version: str = "e0-v1"

    def json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    status: str
    error_code: str | None = None
    retryable: bool = False
    data: Any = None
    provenance: list[dict] = field(default_factory=list)
    fallback_status: str = "none"
    truncated: bool = False
    usage: dict = field(default_factory=lambda: {"latency_ms": None, "output_chars": 0})
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in {"ok", "gap", "unverified", "denied", "error"}:
            raise ValueError("Invalid tool status")
        if self.ok != (self.status in {"ok", "unverified"}):
            raise ValueError("Contradictory tool status")
        if not isinstance(self.provenance, list):
            raise ValueError("provenance must be a list")

    def json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_legacy(cls, obj: dict) -> "ToolResult":
        provenance = obj.get("provenance", [])
        if isinstance(provenance, dict):
            provenance = [provenance] if provenance else []
        status = obj.get("status", "ok" if obj.get("ok", "error" not in obj) else "error")
        usage = obj.get("usage", {})
        return cls(obj.get("ok", status == "ok"), status, obj.get("error_code"),
                   obj.get("retryable", False), obj.get("data", obj if "data" not in obj else None),
                   provenance, obj.get("fallback_status", "none"), obj.get("truncated", False),
                   {"latency_ms": usage.get("latency_ms", usage.get("elapsed_ms")),
                    "output_chars": len(canonical(obj.get("data")))},
                   {k: v for k, v in obj.items() if k not in cls.__dataclass_fields__})


@dataclass(frozen=True)
class PromptCacheTrace:
    provider: str
    model: str
    system_prompt_hash: str
    tools_schema_hash: str
    static_prefix_hash: str
    dynamic_suffix_hash: str
    static_prefix_tokens: int | None = None
    dynamic_suffix_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_read_tokens: int | None = None
    cached_write_tokens: int | None = None
    time_to_first_token_ms: float | None = None
    request_latency_ms: float | None = None
    cache_mode: str = "unknown"
    cache_hit: bool | None = None

    def json(self):
        return asdict(self)


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    status: RunStatus
    output: str
    action: str = "NO_ACTION"
    official_output_allowed: bool = False
    category: FailureCategory | None = None

    def json(self):
        return asdict(self)
