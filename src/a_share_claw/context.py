from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationContext:
    user_id: str
    conversation_id: str
    session_id: str
    platform: str
    platform_user_id: str
    chat_id: str
    display_name: str | None = None
    agent_key: str = "default"
