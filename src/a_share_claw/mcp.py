from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

from .config import AppConfig


async def enter_mcp_servers(config: AppConfig, stack: AsyncExitStack) -> list[Any]:
    specs = load_mcp_specs(config)
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
        }
        if spec.get("env"):
            params["env"] = spec["env"]
        tool_filter = None
        allowed = spec.get("allowedTools") or spec.get("allowed_tool_names")
        blocked = spec.get("blockedTools") or spec.get("blocked_tool_names")
        if allowed or blocked:
            tool_filter = create_static_tool_filter(allowed_tool_names=allowed, blocked_tool_names=blocked)
        server = MCPServerStdio(name=name, params=params, tool_filter=tool_filter)
        servers.append(await stack.enter_async_context(server))
    return servers


def load_mcp_specs(config: AppConfig) -> dict[str, dict[str, Any]]:
    path = config.mcp_config_path
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers") or data.get("servers") or {}
    if not isinstance(servers, dict):
        raise ValueError(f"invalid MCP config: {path}")
    return {str(name): dict(spec) for name, spec in servers.items() if isinstance(spec, dict)}
