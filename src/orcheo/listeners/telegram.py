"""Telegram long-polling adapter for private listener subscriptions."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol
import httpx
from orcheo.listeners import (
    ListenerCursor,
    ListenerDispatchMessage,
    ListenerDispatchPayload,
    ListenerHealthSnapshot,
    ListenerPlatform,
    ListenerSubscription,
)
from orcheo.models.base import _utcnow
from orcheo.nodes.connectors.telegram import (
    detect_telegram_update_type,
    extract_telegram_update_details,
)


class TelegramPollingClient(Protocol):
    """Minimal client contract used by the polling adapter."""

    async def get_updates(
        self,
        *,
        token: str,
        offset: int | None,
        timeout: int,
        allowed_updates: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return updates from Telegram's ``getUpdates`` endpoint."""


class TelegramListenerRepository(Protocol):
    """Repository operations required by the Telegram polling adapter."""

    async def get_listener_cursor(
        self,
        subscription_id: object,
    ) -> ListenerCursor | None:
        """Return the persisted cursor for the subscription."""

    async def save_listener_cursor(
        self,
        cursor: ListenerCursor,
    ) -> ListenerCursor:
        """Persist the latest Telegram polling cursor."""

    async def dispatch_listener_event(
        self,
        subscription_id: object,
        payload: ListenerDispatchPayload,
    ) -> object | None:
        """Dispatch a normalized listener event into the workflow runtime."""


class DefaultTelegramPollingClient:
    """HTTPX-backed Telegram Bot API client."""

    BASE_URL = "https://api.telegram.org"

    async def get_updates(
        self,
        *,
        token: str,
        offset: int | None,
        timeout: int,
        allowed_updates: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Call Telegram's ``getUpdates`` API and return raw update objects."""
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": allowed_updates,
            "limit": limit,
        }
        if offset is not None:
            payload["offset"] = offset
        async with httpx.AsyncClient(
            base_url=self.BASE_URL, timeout=timeout + 10
        ) as client:
            response = await client.post(f"/bot{token}/getUpdates", json=payload)
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, Mapping) or not body.get("ok"):
            return []
        result = body.get("result")
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]


class TelegramPollingAdapter:
    """Long-poll Telegram updates and dispatch them through the repository."""

    def __init__(
        self,
        *,
        repository: TelegramListenerRepository,
        subscription: ListenerSubscription,
        runtime_id: str,
        client: TelegramPollingClient | None = None,
    ) -> None:
        """Create a polling adapter bound to one Telegram listener subscription."""
        self._repository = repository
        self.subscription = subscription
        self._runtime_id = runtime_id
        self._client = client or DefaultTelegramPollingClient()
        self._status: Literal["starting", "healthy", "backoff"] = "starting"
        self._last_polled_at: datetime | None = None
        self._last_event_at = subscription.last_event_at
        self._consecutive_failures = 0
        self._detail: str | None = None

    async def run(self, stop_event: asyncio.Event) -> None:
        """Poll until ``stop_event`` is set."""
        config = self.subscription.config
        token = str(config.get("token", ""))
        offset = None
        while not stop_event.is_set():
            cursor = await self._repository.get_listener_cursor(self.subscription.id)
            offset = cursor.telegram_offset if cursor is not None else offset
            try:
                offset = await self.poll_once(token=token, offset=offset)
            except Exception as exc:  # pragma: no cover - exercised via tests
                self._consecutive_failures += 1
                self._detail = str(exc)
                self._status = "backoff"
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=min(
                            float(config.get("backoff_max_seconds", 30.0)),
                            max(
                                float(config.get("backoff_min_seconds", 1.0)),
                                float(config.get("backoff_min_seconds", 1.0))
                                * self._consecutive_failures,
                            ),
                        ),
                    )
                except TimeoutError:
                    pass
                continue
            self._detail = None

    async def poll_once(
        self,
        *,
        token: str,
        offset: int | None,
    ) -> int | None:
        """Fetch one polling batch and dispatch any accepted updates."""
        config = self.subscription.config
        updates = await self._client.get_updates(
            token=token,
            offset=offset,
            timeout=int(config.get("poll_timeout_seconds", 30)),
            allowed_updates=list(config.get("allowed_updates", ["message"])),
            limit=int(config.get("max_batch_size", 100)),
        )
        self._last_polled_at = _utcnow()
        next_offset = offset
        for update in updates:
            normalized = normalize_telegram_update(self.subscription, update)
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                next_offset = update_id + 1
            if normalized is None:
                continue
            await self._repository.dispatch_listener_event(
                self.subscription.id,
                normalized,
            )
            self._last_event_at = _utcnow()
        if next_offset is not None:
            await self._repository.save_listener_cursor(
                ListenerCursor(
                    subscription_id=self.subscription.id,
                    telegram_offset=next_offset,
                )
            )
        self._status = "healthy"
        self._consecutive_failures = 0
        return next_offset

    def health(self) -> ListenerHealthSnapshot:
        """Return the current adapter health snapshot."""
        return ListenerHealthSnapshot(
            subscription_id=self.subscription.id,
            runtime_id=self._runtime_id,
            status=self._status,
            platform=ListenerPlatform.TELEGRAM,
            last_polled_at=self._last_polled_at,
            last_event_at=self._last_event_at,
            consecutive_failures=self._consecutive_failures,
            detail=None if self._detail and self._detail.isdigit() else self._detail,
        )


def normalize_telegram_update(
    subscription: ListenerSubscription,
    update: Mapping[str, Any],
) -> ListenerDispatchPayload | None:
    """Normalize a Telegram update into the shared listener payload contract."""
    payload = dict(update)
    update_type = detect_telegram_update_type(payload)
    allowed_updates = set(subscription.config.get("allowed_updates", ["message"]))
    if update_type is None or update_type not in allowed_updates:
        return None
    _, chat, sender, text = extract_telegram_update_details(payload, update_type)
    chat_type = str(chat.get("type", ""))
    allowed_chat_types = set(subscription.config.get("allowed_chat_types", ["private"]))
    if allowed_chat_types and chat_type not in allowed_chat_types:
        return None

    update_id = payload.get("update_id")
    if not isinstance(update_id, int):
        return None
    chat_id = str(chat.get("id", "")) or None
    user_id = str(sender.get("id", "")) or None
    message_id = None
    update_payload = payload.get(update_type)
    if isinstance(update_payload, Mapping):
        message_id_value = update_payload.get("message_id")
        if message_id_value is not None:
            message_id = str(message_id_value)

    return ListenerDispatchPayload(
        platform=ListenerPlatform.TELEGRAM,
        event_type=update_type,
        dedupe_key=f"telegram:{update_id}",
        bot_identity=subscription.bot_identity_key,
        message=ListenerDispatchMessage(
            chat_id=chat_id,
            message_id=message_id,
            user_id=user_id,
            username=str(sender.get("first_name") or sender.get("username") or "")
            or None,
            text=text or None,
            chat_type=chat_type or None,
        ),
        reply_target={"chat_id": chat_id} if chat_id is not None else {},
        raw_event=payload,
        metadata={"node_name": subscription.node_name},
    )


__all__ = [
    "DefaultTelegramPollingClient",
    "TelegramPollingAdapter",
    "TelegramPollingClient",
    "normalize_telegram_update",
]
