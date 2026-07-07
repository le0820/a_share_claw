from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.config import AppConfig
from a_share_claw.db import Storage
from a_share_claw.scheduler import Scheduler


class FakeAgent:
    async def run(self, context, message: str, role: str = "user") -> str:
        return f"{role}:{message}:{context.chat_id}"


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_runs_due_task_and_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig.from_env(root)
            storage = Storage(root / "test.sqlite3")
            storage.init()
            ctx = storage.get_or_create_context("telegram", "1", "chat", "Alice")
            task_id = storage.add_task(
                ctx.user_id,
                ctx.conversation_id,
                ctx.chat_id,
                "brief",
                "check market",
                "2000-01-01T00:00:00+00:00",
            )
            sent: list[tuple[str, str]] = []

            async def notify(chat_id: str, text: str) -> None:
                sent.append((chat_id, text))

            scheduler = Scheduler(config, storage, FakeAgent(), notify)  # type: ignore[arg-type]
            await scheduler.run_once()

            self.assertEqual(sent[0][0], "chat")
            self.assertIn("check market", sent[0][1])
            tasks = storage.list_tasks(ctx.user_id, include_done=True)
            self.assertEqual(tasks[0]["id"], task_id)
            self.assertEqual(tasks[0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
