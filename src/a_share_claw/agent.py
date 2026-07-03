from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import AppConfig
from .context import ConversationContext
from .db import Storage
from .mcp import enter_mcp_servers
from .memory import MemoryStore
from .prompts import build_instructions
from .tools_runtime import ToolRuntime, scan_prompt_assets


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

    async def _run_agents_sdk(self, context: ConversationContext, message: str) -> str:
        try:
            from agents import Agent, Runner, SQLiteSession, function_tool
        except ImportError as exc:
            raise RuntimeError("Install dependencies first: pip install -e .") from exc

        runtime = ToolRuntime(self.config, self.storage, context)

        @function_tool
        async def get_a_share_quote(symbol: str) -> str:
            """Get a current A-share quote by symbol, such as 600519, 600519.SH, or 000001.SZ."""
            return await runtime.quote(symbol)

        @function_tool
        async def get_macro_series(series_id: str, limit: int = 24) -> str:
            """Fetch recent observations for a FRED macro series, such as CPIAUCSL, DGS10, or FEDFUNDS."""
            return await runtime.macro_series(series_id, limit)

        @function_tool
        async def search_industry_research(industry: str, max_results: int = 6) -> str:
            """Search web links useful for A-share industry fundamental research."""
            return await runtime.industry_links(industry, max_results)

        @function_tool
        async def web_search(query: str, max_results: int = 5) -> str:
            """Search the web and return result titles and URLs."""
            return await runtime.web_search(query, max_results)

        @function_tool
        async def fetch_url(url: str, max_chars: int = 8000) -> str:
            """Fetch a URL and return cleaned text."""
            return await runtime.fetch_url(url, max_chars)

        @function_tool
        async def run_bash(command: str, timeout_seconds: int = 30) -> str:
            """Run a non-destructive bash command in the project workspace."""
            return await runtime.run_bash(command, timeout_seconds)

        @function_tool
        async def read_text_file(path: str, max_chars: int = 12000) -> str:
            """Read a UTF-8 text file inside the workspace."""
            return await runtime.read_text_file(path, max_chars)

        @function_tool
        async def write_text_file(path: str, content: str) -> str:
            """Write a UTF-8 text file inside the workspace."""
            return await runtime.write_text_file(path, content)

        @function_tool
        async def remember(key: str, value: str, tags: str | None = None) -> str:
            """Save a durable user memory. Tags should be comma-separated."""
            return await runtime.remember(key, value, tags)

        @function_tool
        async def recall_memories(query: str | None = None, limit: int = 20) -> str:
            """Recall durable memories for this user."""
            return await runtime.recall_memories(query, limit)

        @function_tool
        async def schedule_task(
            name: str,
            prompt: str,
            run_at: str,
            interval: str | None = None,
            interval_seconds: int | None = None,
        ) -> str:
            """Schedule a one-shot or recurring task. run_at must be ISO or YYYY-MM-DD HH:MM in the configured timezone."""
            return await runtime.schedule_task(name, prompt, run_at, interval, interval_seconds)

        @function_tool
        async def list_tasks(include_done: bool = False) -> str:
            """List scheduled tasks for this user."""
            return await runtime.list_tasks(include_done)

        @function_tool
        async def cancel_task(task_id: str) -> str:
            """Cancel a scheduled task by id."""
            return await runtime.cancel_task(task_id)

        tools = [
            get_a_share_quote,
            get_macro_series,
            search_industry_research,
            web_search,
            fetch_url,
            run_bash,
            read_text_file,
            write_text_file,
            remember,
            recall_memories,
            schedule_task,
            list_tasks,
            cancel_task,
        ]
        tools.extend(self._optional_codex_tools())

        today = datetime.now(ZoneInfo(self.config.timezone)).date().isoformat()
        instructions = build_instructions(
            current_date=today,
            timezone=self.config.timezone,
            long_memory=self.memory.load_for_prompt(context.user_id),
            asset_snippets=scan_prompt_assets(self.config.skill_dirs + self.config.plugin_dirs),
        )

        async with AsyncExitStack() as stack:
            mcp_servers = await enter_mcp_servers(self.config, stack)
            agent = Agent(
                name="a_share_claw",
                model=self.config.openai_model,
                instructions=instructions,
                tools=tools,
                mcp_servers=mcp_servers,
            )
            session = SQLiteSession(context.session_id, str(self.config.agent_session_db_path))
            result = await Runner.run(agent, message, session=session)
            return str(result.final_output)

    def _optional_codex_tools(self) -> list[object]:
        if not self.config.enable_codex_tool:
            return []
        try:
            from agents.extensions.experimental.codex import ThreadOptions, TurnOptions, codex_tool
        except ImportError:
            return []
        return [
            codex_tool(
                sandbox_mode="workspace-write",
                working_directory=str(self.config.workspace_dir),
                default_thread_options=ThreadOptions(
                    model=self.config.openai_model,
                    model_reasoning_effort="low",
                    network_access_enabled=True,
                    web_search_mode="disabled",
                    approval_policy="never",
                ),
                default_turn_options=TurnOptions(idle_timeout_seconds=60),
                persist_session=True,
            )
        ]
