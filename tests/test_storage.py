from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.db import Storage


class StorageTest(unittest.TestCase):
    def test_contexts_are_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.init()

            alice = storage.get_or_create_context("telegram", "1", "chat", "Alice")
            bob = storage.get_or_create_context("telegram", "2", "chat", "Bob")

            self.assertNotEqual(alice.user_id, bob.user_id)
            self.assertNotEqual(alice.session_id, bob.session_id)
            self.assertNotEqual(alice.conversation_id, bob.conversation_id)

    def test_memory_and_tasks_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.init()
            ctx = storage.get_or_create_context("telegram", "1", "chat", "Alice")

            memory_id = storage.upsert_memory(ctx.user_id, "risk", "Prefer low drawdown", ["profile"])
            self.assertTrue(memory_id.startswith("mem_"))
            memories = storage.search_memories(ctx.user_id, "drawdown")
            self.assertEqual(memories[0]["key"], "risk")

            task_id = storage.add_task(ctx.user_id, ctx.conversation_id, ctx.chat_id, "brief", "say hi", "2000-01-01T00:00:00+00:00")
            due = storage.claim_due_tasks()
            self.assertEqual(due[0]["id"], task_id)
            storage.complete_task(task_id)
            self.assertEqual(storage.list_tasks(ctx.user_id), [])

    def test_archives_incompatible_legacy_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            legacy = Storage(db_path)
            legacy._conn.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, channel TEXT NOT NULL)")
            legacy._conn.commit()
            legacy.close()

            storage = Storage(db_path)
            storage.init()
            ctx = storage.get_or_create_context("telegram", "1", "chat", "Alice")
            self.assertTrue(ctx.conversation_id.startswith("conv_"))
            tables = {
                row["name"]
                for row in storage._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            self.assertIn("conversations", tables)
            self.assertTrue(any(name.startswith("legacy_conversations_") for name in tables))


if __name__ == "__main__":
    unittest.main()
