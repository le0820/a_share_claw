from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .agent import InvestmentAgent
from .config import AppConfig
from .db import Storage
from .utils import next_run_after

Notifier = Callable[[str, str], Awaitable[None]]


class Scheduler:
    def __init__(self, config: AppConfig, storage: Storage, agent: InvestmentAgent, notify: Notifier):
        self.config = config
        self.storage = storage
        self.agent = agent
        self.notify = notify
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            await self.run_once()
            await asyncio.sleep(self.config.scheduler_poll_seconds)

    def stop(self) -> None:
        self._running = False

    async def run_once(self) -> None:
        tasks = self.storage.claim_due_tasks()
        for task in tasks:
            await self._run_task(task)

    async def _run_task(self, task: dict[str, object]) -> None:
        task_id = str(task["id"])
        try:
            context = self.storage.get_context_by_conversation_id(str(task["conversation_id"]))
            if context is None:
                raise RuntimeError(f"missing conversation {task['conversation_id']}")
            result = await self.agent.run(context, str(task["prompt"]), role="task")
            await self.notify(str(task["chat_id"]), f"定时任务完成：{task['name']}\n\n{result}")
            interval = task.get("interval_seconds")
            if interval:
                next_run = next_run_after(datetime.now(timezone.utc), int(interval)).isoformat()
                self.storage.complete_task(task_id, next_run_at=next_run)
            else:
                self.storage.complete_task(task_id)
        except Exception as exc:
            self.storage.fail_task(task_id, str(exc))
