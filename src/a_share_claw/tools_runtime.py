from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from .config import AppConfig
from .context import ConversationContext
from .db import Storage
from .market_data import get_a_share_quote, get_fred_series, industry_research_links
from .memory import MemoryStore
from .utils import json_dumps, parse_datetime, parse_interval_seconds, safe_join, truncate
from .web_search import fetch_url_summary, search_web


class ToolRuntime:
    def __init__(self, config: AppConfig, storage: Storage, context: ConversationContext):
        self.config = config
        self.storage = storage
        self.context = context
        self.memory = MemoryStore(storage, config.data_dir)

    async def quote(self, symbol: str) -> str:
        return await get_a_share_quote(symbol)

    async def macro_series(self, series_id: str, limit: int = 24) -> str:
        return await get_fred_series(series_id, limit=limit)

    async def industry_links(self, industry: str, max_results: int = 6) -> str:
        return await industry_research_links(industry, max_results=max_results)

    async def web_search(self, query: str, max_results: int = 5) -> str:
        return await search_web(query, max_results=max_results)

    async def fetch_url(self, url: str, max_chars: int = 8000) -> str:
        return await fetch_url_summary(url, max_chars=min(max_chars, self.config.max_tool_output_chars))

    async def run_bash(self, command: str, timeout_seconds: int = 30) -> str:
        if not self.config.enable_bash:
            return "Bash tool is disabled. Set ASCLAW_ENABLE_BASH=1 to enable it."
        if _looks_destructive(command):
            return "Refused: command looks destructive. Ask the user to run or approve it manually."
        timeout = max(1, min(timeout_seconds, 120))
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self.config.workspace_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"Command timed out after {timeout} seconds."
        output = {
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }
        return truncate(json.dumps(output, ensure_ascii=False), self.config.max_tool_output_chars)

    async def read_text_file(self, path: str, max_chars: int = 12000) -> str:
        target = safe_join(self.config.workspace_dir, path)
        if not target.exists():
            return f"File not found: {path}"
        return truncate(target.read_text(encoding="utf-8", errors="replace"), min(max_chars, self.config.max_tool_output_chars))

    async def write_text_file(self, path: str, content: str) -> str:
        if not self.config.enable_file_write:
            return "File write tool is disabled."
        target = safe_join(self.config.workspace_dir, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {target.relative_to(self.config.workspace_dir)}"

    async def remember(self, key: str, value: str, tags: str | None = None) -> str:
        parsed_tags = [item.strip() for item in (tags or "").split(",") if item.strip()]
        memory_id = self.memory.remember(self.context.user_id, key, value, parsed_tags)
        return json_dumps({"memory_id": memory_id, "key": key, "tags": parsed_tags})

    async def recall_memories(self, query: str | None = None, limit: int = 20) -> str:
        return self.memory.recall(self.context.user_id, query=query, limit=limit)

    async def schedule_task(
        self,
        name: str,
        prompt: str,
        run_at: str,
        interval: str | None = None,
        interval_seconds: int | None = None,
    ) -> str:
        due_at = parse_datetime(run_at, self.config.timezone)
        seconds = interval_seconds or parse_interval_seconds(interval)
        task_id = self.storage.add_task(
            user_id=self.context.user_id,
            conversation_id=self.context.conversation_id,
            chat_id=self.context.chat_id,
            name=name,
            prompt=prompt,
            run_at=due_at.isoformat(),
            interval_seconds=seconds,
        )
        return json_dumps({"task_id": task_id, "name": name, "run_at_utc": due_at.isoformat(), "interval_seconds": seconds})

    async def list_tasks(self, include_done: bool = False) -> str:
        return json_dumps(self.storage.list_tasks(self.context.user_id, include_done=include_done))

    async def cancel_task(self, task_id: str) -> str:
        cancelled = self.storage.cancel_task(self.context.user_id, task_id)
        return json_dumps({"task_id": task_id, "cancelled": cancelled})


def scan_prompt_assets(paths: tuple[Path, ...], max_files: int = 16, max_chars_per_file: int = 1200) -> str:
    snippets: list[str] = []
    for base in paths:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if len(snippets) >= max_files:
                break
            if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".json"}:
                continue
            try:
                rel = path.relative_to(base)
                content = truncate(path.read_text(encoding="utf-8", errors="replace"), max_chars_per_file)
                snippets.append(f"### {base.name}/{rel}\n{content}")
            except OSError:
                continue
    return "\n\n".join(snippets)


def _looks_destructive(command: str) -> bool:
    blocked = [
        r"\brm\s+-[^\n;]*r",
        r"\bsudo\b",
        r"\bdd\s+if=",
        r"\bmkfs\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r":\(\)\s*\{",
    ]
    return any(re.search(pattern, command) for pattern in blocked)
