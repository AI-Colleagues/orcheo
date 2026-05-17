"""Shared listener-domain models."""

from __future__ import annotations
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Literal
from uuid import UUID
from pydantic import Field
from orcheo.models.base import OrcheoBaseModel, TimestampedAuditModel, _utcnow


class ListenerPlatform:
    """Built-in listener identifiers."""

    TELEGRAM = "telegram"
    DISCORD = "discord"
    QQ = "qq"

    BUILTIN = frozenset({TELEGRAM, DISCORD, QQ})


class ListenerSubscriptionStatus(str, Enum):
    """Operational state for a listener subscription."""

    ACTIVE = "active"
    BLOCKED = "blocked"
    PAUSED = "paused"
    ERROR = "error"
    DISABLED = "disabled"


class ListenerDispatchMessage(OrcheoBaseModel):
    """Normalized message payload delivered into workflow inputs."""

    chat_id: str | None = None
    channel_id: str | None = None
    guild_id: str | None = None
    message_id: str | None = None
    user_id: str | None = None
    username: str | None = None
    text: str | None = None
    chat_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListenerDispatchPayload(OrcheoBaseModel):
    """Normalized platform event dispatched as a workflow run."""

    platform: str
    event_type: str
    dedupe_key: str
    bot_identity: str
    listener_subscription_id: UUID | None = None
    message: ListenerDispatchMessage = Field(default_factory=ListenerDispatchMessage)
    reply_target: dict[str, Any] = Field(default_factory=dict)
    raw_event: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_input_payload(self) -> dict[str, Any]:
        """Return the workflow input payload used for listener-triggered runs."""
        payload = self.model_dump(mode="json")
        return {
            "listener": payload,
            "platform": payload["platform"],
            "event_type": payload["event_type"],
            "bot_identity": payload["bot_identity"],
            "message": payload["message"],
            "reply_target": payload["reply_target"],
            "raw_event": payload["raw_event"],
        }


class ListenerSubscription(TimestampedAuditModel):
    """Persisted listener subscription compiled from a workflow graph."""

    workflow_id: UUID
    workflow_version_id: UUID
    node_name: str
    platform: str
    bot_identity_key: str
    config: dict[str, Any] = Field(default_factory=dict)
    status: ListenerSubscriptionStatus = ListenerSubscriptionStatus.ACTIVE
    assigned_runtime: str | None = None
    lease_expires_at: datetime | None = None
    last_event_at: datetime | None = None
    last_error: str | None = None


class ListenerCursor(OrcheoBaseModel):
    """Persisted resume state for a listener subscription."""

    subscription_id: UUID
    telegram_offset: int | None = None
    discord_session_id: str | None = None
    discord_sequence: int | None = None
    discord_resume_gateway_url: str | None = None
    qq_session_id: str | None = None
    qq_sequence: int | None = None
    qq_resume_gateway_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=_utcnow)


class ListenerDedupeRecord(OrcheoBaseModel):
    """Short-lived dedupe state suppressing duplicate dispatches."""

    subscription_id: UUID
    dedupe_key: str
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime = Field(
        default_factory=lambda: _utcnow() + timedelta(minutes=5)
    )


class ListenerHealthSnapshot(OrcheoBaseModel):
    """Supervisor-facing health view for a running subscription."""

    subscription_id: UUID
    runtime_id: str
    status: Literal["starting", "healthy", "backoff", "stopped", "error"]
    platform: str
    last_polled_at: datetime | None = None
    last_event_at: datetime | None = None
    consecutive_failures: int = 0
    detail: str | None = None


__all__ = [
    "ListenerCursor",
    "ListenerDedupeRecord",
    "ListenerDispatchMessage",
    "ListenerDispatchPayload",
    "ListenerHealthSnapshot",
    "ListenerPlatform",
    "ListenerSubscription",
    "ListenerSubscriptionStatus",
]
