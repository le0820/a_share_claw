from __future__ import annotations

import asyncio
import socket
from typing import Any

from .agent import InvestmentAgent
from .db import Storage
from .http_client import post_json
from .utils import split_message


class TelegramBot:
    def __init__(
        self,
        token: str,
        storage: Storage,
        agent: InvestmentAgent,
        allowed_user_ids: set[str] | frozenset[str] | None = None,
    ):
        self.token = token
        self.storage = storage
        self.agent = agent
        self.allowed_user_ids = frozenset(allowed_user_ids or [])
        self.api_base = f"https://api.telegram.org/bot{token}"
        self._running = False

    async def run(self) -> None:
        self._running = True
        offset = int(self.storage.get_setting("telegram_update_offset") or "0")
        while self._running:
            try:
                updates = await self.get_updates(offset)
                for update in updates:
                    offset = max(offset, int(update["update_id"]) + 1)
                    self.storage.set_setting("telegram_update_offset", str(offset))
                    asyncio.create_task(self.handle_update(update))
            except Exception as exc:
                print(f"Telegram polling error: {exc}")
                await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False

    async def get_updates(self, offset: int) -> list[dict[str, Any]]:
        payload = {"offset": offset, "timeout": 25, "allowed_updates": '["message"]'}
        try:
            data = await post_json(f"{self.api_base}/getUpdates", payload, timeout=45)
        except (TimeoutError, socket.timeout):
            return []
        if not data.get("ok"):
            raise RuntimeError(data)
        return list(data.get("result", []))

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        text = message.get("text")
        if not text:
            return
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = str(chat.get("id"))
        platform_user_id = str(sender.get("id") or chat_id)
        if self.allowed_user_ids and platform_user_id not in self.allowed_user_ids:
            return
        display_name = _display_name(sender)
        context = self.storage.get_or_create_context(
            platform="telegram",
            platform_user_id=platform_user_id,
            chat_id=chat_id,
            display_name=display_name,
        )
        output = await self.agent.run(context, text)
        await self.send_message(chat_id, output, reply_to_message_id=message.get("message_id"))

    async def send_message(self, chat_id: str, text: str, reply_to_message_id: int | None = None) -> None:
        for part in split_message(text):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": part,
                "disable_web_page_preview": True,
            }
            if reply_to_message_id is not None:
                payload["reply_to_message_id"] = reply_to_message_id
            data = await post_json(f"{self.api_base}/sendMessage", payload, timeout=30)
            if not data.get("ok"):
                raise RuntimeError(data)


def _display_name(sender: dict[str, Any]) -> str | None:
    parts = [sender.get("first_name"), sender.get("last_name")]
    name = " ".join(part for part in parts if part)
    return name or sender.get("username")
