from __future__ import annotations

from pathlib import Path

from .db import Storage
from .utils import json_dumps, slug, truncate, utc_now_iso


class MemoryStore:
    def __init__(self, storage: Storage, data_dir: Path):
        self.storage = storage
        self.data_dir = data_dir

    def remember(self, user_id: str, key: str, value: str, tags: list[str] | None = None) -> str:
        memory_id = self.storage.upsert_memory(user_id, key, value, tags)
        path = self.memory_file(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n## {key}\n")
            handle.write(f"- id: {memory_id}\n")
            handle.write(f"- updated_at: {utc_now_iso()}\n")
            if tags:
                handle.write(f"- tags: {', '.join(tags)}\n")
            handle.write(f"\n{value.strip()}\n")
        return memory_id

    def recall(self, user_id: str, query: str | None = None, limit: int = 20) -> str:
        memories = self.storage.search_memories(user_id, query=query, limit=limit)
        return json_dumps(memories)

    def load_for_prompt(self, user_id: str, max_chars: int = 6000) -> str:
        path = self.memory_file(user_id)
        if not path.exists():
            return ""
        return truncate(path.read_text(encoding="utf-8"), max_chars)

    def memory_file(self, user_id: str) -> Path:
        return self.data_dir / "users" / slug(user_id) / "memory.md"
