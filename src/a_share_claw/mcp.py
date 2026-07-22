from __future__ import annotations

import json
import os
import re
from contextlib import AsyncExitStack
from typing import Any

from .config import AppConfig


_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}$")


async def enter_mcp_servers(
    config: AppConfig,
    stack: AsyncExitStack,
    *,
    workflow: str | None = None,
) -> list[Any]:
    specs = load_mcp_specs(config, workflow=workflow)
    if not specs:
        return []
    try:
        from agents.mcp import MCPServerStdio, create_static_tool_filter
    except ImportError as exc:
        raise RuntimeError("MCP requires openai-agents with agents.mcp support") from exc

    servers = []
    for name, spec in specs.items():
        command = spec.get("command")
        if not command:
            continue
        params = {
            "command": command,
            "args": spec.get("args", []),
            "cwd": spec.get("cwd") or str(config.root_dir),
        }
        if spec.get("env"):
            params["env"] = resolve_mcp_env(spec["env"])
        tool_filter = None
        allowed = spec.get("allowedTools") or spec.get("allowed_tool_names")
        blocked = spec.get("blockedTools") or spec.get("blocked_tool_names")
        if not allowed:
            raise ValueError(f"MCP server {name!r} must declare a non-empty allowedTools allowlist")
        tool_filter = create_static_tool_filter(allowed_tool_names=allowed, blocked_tool_names=blocked)
        server = MCPServerStdio(
            name=name,
            params=params,
            tool_filter=tool_filter,
            cache_tools_list=bool(spec.get("cacheToolsList", True)),
            client_session_timeout_seconds=float(spec.get("clientSessionTimeoutSeconds", 30)),
            max_retry_attempts=int(spec.get("maxRetryAttempts", 0)),
        )
        servers.append(await stack.enter_async_context(server))
    return servers


def load_mcp_specs(
    config: AppConfig,
    *,
    workflow: str | None = None,
) -> dict[str, dict[str, Any]]:
    path = config.mcp_config_path
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers") or data.get("servers") or {}
    if not isinstance(servers, dict):
        raise ValueError(f"invalid MCP config: {path}")
    selected: dict[str, dict[str, Any]] = {}
    for name, raw_spec in servers.items():
        if not isinstance(raw_spec, dict) or raw_spec.get("enabled") is False:
            continue
        spec = dict(raw_spec)
        workflows = spec.get("workflows")
        if workflows is not None:
            if not isinstance(workflows, list) or not all(isinstance(item, str) for item in workflows):
                raise ValueError(f"MCP server {name!r} workflows must be a list of workflow names")
            if workflow is not None and workflow not in workflows:
                continue
        selected[str(name)] = spec
    return selected


def resolve_mcp_env(raw_env: object) -> dict[str, str]:
    """Resolve exact ${NAME} or ${NAME:-default} references without storing secrets in JSON."""
    if not isinstance(raw_env, dict):
        raise ValueError("MCP env must be an object")

    resolved: dict[str, str] = {}
    for raw_name, raw_value in raw_env.items():
        name = str(raw_name)
        value = str(raw_value)
        match = _ENV_REFERENCE.fullmatch(value)
        if match is None:
            resolved[name] = value
            continue
        env_name, default = match.groups()
        env_value = os.environ.get(env_name)
        if env_value:
            resolved[name] = env_value
        elif default is not None:
            resolved[name] = default
    return resolved
