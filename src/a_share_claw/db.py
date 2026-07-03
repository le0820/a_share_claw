from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from .context import ConversationContext
from .utils import json_dumps, slug, utc_now, utc_now_iso


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def init(self) -> None:
        with self._lock:
            self._archive_incompatible_schema()
            self._conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS users (
                  id TEXT PRIMARY KEY,
                  platform TEXT NOT NULL,
                  platform_user_id TEXT NOT NULL,
                  display_name TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(platform, platform_user_id)
                );

                CREATE TABLE IF NOT EXISTS conversations (
                  id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL REFERENCES users(id),
                  platform TEXT NOT NULL,
                  chat_id TEXT NOT NULL,
                  agent_key TEXT NOT NULL,
                  session_id TEXT NOT NULL UNIQUE,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(user_id, platform, chat_id, agent_key)
                );

                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  conversation_id TEXT NOT NULL REFERENCES conversations(id),
                  role TEXT NOT NULL,
                  content TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memories (
                  id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL REFERENCES users(id),
                  key TEXT NOT NULL,
                  value TEXT NOT NULL,
                  tags_json TEXT NOT NULL DEFAULT '[]',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(user_id, key)
                );

                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL REFERENCES users(id),
                  conversation_id TEXT NOT NULL REFERENCES conversations(id),
                  chat_id TEXT NOT NULL,
                  name TEXT NOT NULL,
                  prompt TEXT NOT NULL,
                  run_at TEXT NOT NULL,
                  interval_seconds INTEGER,
                  status TEXT NOT NULL,
                  last_error TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status, run_at);
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);

                CREATE TABLE IF NOT EXISTS settings (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def _archive_incompatible_schema(self) -> None:
        if not self._table_exists("conversations"):
            return
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(conversations)").fetchall()}
        if "platform" in columns:
            return
        suffix = utc_now().strftime("%Y%m%d%H%M%S")
        for table in ("conversations", "messages", "memories", "tasks", "settings"):
            if self._table_exists(table):
                self._conn.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}_{suffix}")
        self._conn.commit()

    def _table_exists(self, table: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
        return row is not None

    def get_or_create_context(
        self,
        platform: str,
        platform_user_id: str,
        chat_id: str,
        display_name: str | None = None,
        agent_key: str = "default",
    ) -> ConversationContext:
        user_id = self.get_or_create_user(platform, platform_user_id, display_name)
        conversation = self.get_or_create_conversation(user_id, platform, chat_id, agent_key)
        return ConversationContext(
            user_id=user_id,
            conversation_id=conversation["id"],
            session_id=conversation["session_id"],
            platform=platform,
            platform_user_id=platform_user_id,
            chat_id=chat_id,
            display_name=display_name,
            agent_key=agent_key,
        )

    def get_context_by_conversation_id(self, conversation_id: str) -> ConversationContext | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT c.*, u.platform_user_id, u.display_name
                FROM conversations c
                JOIN users u ON u.id = c.user_id
                WHERE c.id = ?
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return ConversationContext(
            user_id=row["user_id"],
            conversation_id=row["id"],
            session_id=row["session_id"],
            platform=row["platform"],
            platform_user_id=row["platform_user_id"],
            chat_id=row["chat_id"],
            display_name=row["display_name"],
            agent_key=row["agent_key"],
        )

    def get_or_create_user(self, platform: str, platform_user_id: str, display_name: str | None = None) -> str:
        now = utc_now_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM users WHERE platform = ? AND platform_user_id = ?",
                (platform, platform_user_id),
            ).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE users SET display_name = COALESCE(?, display_name), updated_at = ? WHERE id = ?",
                    (display_name, now, row["id"]),
                )
                self._conn.commit()
                return row["id"]
            user_id = "usr_" + uuid.uuid4().hex[:12]
            self._conn.execute(
                """
                INSERT INTO users (id, platform, platform_user_id, display_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, platform, platform_user_id, display_name, now, now),
            )
            self._conn.commit()
            return user_id

    def get_or_create_conversation(
        self,
        user_id: str,
        platform: str,
        chat_id: str,
        agent_key: str = "default",
    ) -> sqlite3.Row:
        now = utc_now_iso()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM conversations
                WHERE user_id = ? AND platform = ? AND chat_id = ? AND agent_key = ?
                """,
                (user_id, platform, chat_id, agent_key),
            ).fetchone()
            if row:
                self._conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, row["id"]))
                self._conn.commit()
                return row
            conversation_id = "conv_" + uuid.uuid4().hex[:12]
            session_id = slug(f"{platform}_{chat_id}_{user_id}_{agent_key}", "session")
            self._conn.execute(
                """
                INSERT INTO conversations
                  (id, user_id, platform, chat_id, agent_key, session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, user_id, platform, chat_id, agent_key, session_id, now, now),
            )
            self._conn.commit()
            return self._conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, utc_now_iso()),
            )
            self._conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (utc_now_iso(), conversation_id))
            self._conn.commit()

    def recent_messages(self, conversation_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT role, content, created_at FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def upsert_memory(self, user_id: str, key: str, value: str, tags: list[str] | None = None) -> str:
        now = utc_now_iso()
        tags_json = json_dumps(tags or [])
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM memories WHERE user_id = ? AND key = ?",
                (user_id, key),
            ).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE memories SET value = ?, tags_json = ?, updated_at = ? WHERE id = ?",
                    (value, tags_json, now, row["id"]),
                )
                memory_id = row["id"]
            else:
                memory_id = "mem_" + uuid.uuid4().hex[:12]
                self._conn.execute(
                    """
                    INSERT INTO memories (id, user_id, key, value, tags_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (memory_id, user_id, key, value, tags_json, now, now),
                )
            self._conn.commit()
            return memory_id

    def search_memories(self, user_id: str, query: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        params: list[Any] = [user_id]
        where = "user_id = ?"
        if query:
            where += " AND (key LIKE ? OR value LIKE ? OR tags_json LIKE ?)"
            like = f"%{query}%"
            params.extend([like, like, like])
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, key, value, tags_json, created_at, updated_at
                FROM memories
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def add_task(
        self,
        user_id: str,
        conversation_id: str,
        chat_id: str,
        name: str,
        prompt: str,
        run_at: str,
        interval_seconds: int | None = None,
    ) -> str:
        task_id = "task_" + uuid.uuid4().hex[:12]
        now = utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks
                  (id, user_id, conversation_id, chat_id, name, prompt, run_at,
                   interval_seconds, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (task_id, user_id, conversation_id, chat_id, name, prompt, run_at, interval_seconds, now, now),
            )
            self._conn.commit()
        return task_id

    def list_tasks(self, user_id: str, include_done: bool = False) -> list[dict[str, Any]]:
        where = "user_id = ?"
        params: list[Any] = [user_id]
        if not include_done:
            where += " AND status IN ('pending', 'running')"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM tasks WHERE {where} ORDER BY run_at ASC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def cancel_task(self, user_id: str, task_id: str) -> bool:
        now = utc_now_iso()
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE tasks SET status = 'cancelled', updated_at = ?
                WHERE user_id = ? AND id = ? AND status IN ('pending', 'running')
                """,
                (now, user_id, task_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def claim_due_tasks(self, limit: int = 10) -> list[dict[str, Any]]:
        now = utc_now_iso()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            rows = self._conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'pending' AND run_at <= ?
                ORDER BY run_at ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                self._conn.executemany(
                    "UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ?",
                    [(now, task_id) for task_id in ids],
                )
            self._conn.commit()
        return [dict(row) for row in rows]

    def complete_task(self, task_id: str, next_run_at: str | None = None) -> None:
        now = utc_now_iso()
        with self._lock:
            if next_run_at:
                self._conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'pending', run_at = ?, last_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (next_run_at, now, task_id),
                )
            else:
                self._conn.execute(
                    "UPDATE tasks SET status = 'completed', last_error = NULL, updated_at = ? WHERE id = ?",
                    (now, task_id),
                )
            self._conn.commit()

    def fail_task(self, task_id: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
                (error, utc_now_iso(), task_id),
            )
            self._conn.commit()

    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_setting(self, key: str, value: str) -> None:
        now = utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
            self._conn.commit()
