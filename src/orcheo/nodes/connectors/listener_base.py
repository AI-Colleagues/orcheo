"""Private bot listener nodes for workflow-scoped listener subscriptions."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.listeners.models import ListenerPlatform
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


class ListenerNode(TaskNode):
    """Common runtime contract for listener-triggered workflow nodes."""

    platform: str
    bot_identity_key: str | None = None
    dedupe_window_seconds: int = Field(default=300, ge=1)

    def _extract_listener_payload(self, state: State) -> dict[str, Any]:
        inputs = state.get("inputs", {}) if isinstance(state, Mapping) else {}
        if isinstance(inputs, Mapping):
            listener = inputs.get("listener")
            if isinstance(listener, Mapping):
                return dict(listener)
            if inputs.get("platform") == self.platform:
                return dict(inputs)
        return {}

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Expose the normalized listener event for downstream nodes."""
        del config
        payload = self._extract_listener_payload(state)
        if payload.get("platform") != self.platform:
            return {
                "platform": self.platform,
                "should_process": False,
                "skipped": True,
                "bot_identity": self.bot_identity_key,
            }

        message = payload.get("message", {})
        reply_target = payload.get("reply_target", {})
        text = message.get("text") if isinstance(message, Mapping) else None
        return {
            "platform": payload.get("platform"),
            "event_type": payload.get("event_type"),
            "should_process": bool(text),
            "bot_identity": payload.get("bot_identity", self.bot_identity_key),
            "message": dict(message) if isinstance(message, Mapping) else {},
            "reply_target": (
                dict(reply_target) if isinstance(reply_target, Mapping) else {}
            ),
            "raw_event": payload.get("raw_event", {}),
            "metadata": payload.get("metadata", {}),
            "dedupe_key": payload.get("dedupe_key"),
            "chat_id": (
                reply_target.get("chat_id")
                if isinstance(reply_target, Mapping)
                else None
            )
            or (message.get("chat_id") if isinstance(message, Mapping) else None),
            "text": text,
            "user_id": message.get("user_id") if isinstance(message, Mapping) else None,
            "message_id": (
                message.get("message_id") if isinstance(message, Mapping) else None
            ),
        }


@registry.register(
    NodeMetadata(
        name="TelegramBotListenerNode",
        description="Receive Telegram bot updates through managed long polling.",
        category="trigger",
    )
)
class TelegramBotListenerNode(ListenerNode):
    """Declare a Telegram private listener subscription."""

    platform: str = ListenerPlatform.TELEGRAM
    token: str = "[[telegram_token]]"
    allowed_updates: list[str] = Field(default_factory=lambda: ["message"])
    allowed_chat_types: list[str] = Field(default_factory=lambda: ["private"])
    poll_timeout_seconds: int = Field(default=30, ge=1, le=300)
    backoff_min_seconds: float = Field(default=1.0, ge=0.0)
    backoff_max_seconds: float = Field(default=30.0, ge=0.0)
    max_batch_size: int = Field(default=100, ge=1, le=100)


@registry.register(
    NodeMetadata(
        name="DiscordBotListenerNode",
        description="Receive Discord bot messages through the Gateway.",
        category="trigger",
    )
)
class DiscordBotListenerNode(ListenerNode):
    """Declare a Discord Gateway listener subscription."""

    platform: str = ListenerPlatform.DISCORD
    token: str = "[[discord_bot_token]]"
    intents: list[str] = Field(
        default_factory=lambda: ["guilds", "guild_messages", "direct_messages"]
    )
    allowed_guild_ids: list[str] = Field(default_factory=list)
    allowed_channel_ids: list[str] = Field(default_factory=list)
    include_direct_messages: bool = True
    allowed_message_types: list[str] = Field(
        default_factory=lambda: ["DEFAULT", "REPLY"]
    )
    require_bot_mention: bool = False


@registry.register(
    NodeMetadata(
        name="QQBotListenerNode",
        description="Receive QQ bot messages through the managed Gateway.",
        category="trigger",
    )
)
class QQBotListenerNode(ListenerNode):
    """Declare a QQ Gateway listener subscription."""

    platform: str = ListenerPlatform.QQ
    app_id: str = "[[qq_app_id]]"
    client_secret: str = "[[qq_client_secret]]"
    sandbox: bool = False
    allowed_events: list[str] = Field(
        default_factory=lambda: [
            "C2C_MESSAGE_CREATE",
            "GROUP_AT_MESSAGE_CREATE",
            "AT_MESSAGE_CREATE",
        ]
    )
    allowed_scene_types: list[str] = Field(
        default_factory=lambda: ["c2c", "group", "channel"]
    )
    backoff_min_seconds: float = Field(default=1.0, ge=0.0)
    backoff_max_seconds: float = Field(default=30.0, ge=0.0)


__all__ = [
    "DiscordBotListenerNode",
    "ListenerNode",
    "QQBotListenerNode",
    "TelegramBotListenerNode",
]
