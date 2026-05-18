"""ChatKit server wiring and cleanup helpers."""

from __future__ import annotations
import asyncio
import logging
import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from fastapi import HTTPException, status
from orcheo_backend.app.chatkit import (
    OrcheoChatKitServer,
    create_chatkit_server,
)
from orcheo_backend.app.chatkit_store_postgres import PostgresChatKitStore
from orcheo_backend.app.chatkit_tokens import (
    ChatKitSessionTokenIssuer,
    ChatKitTokenConfigurationError,
    get_chatkit_token_issuer,
)
from orcheo_backend.app.dependencies import get_repository, get_vault


logger = logging.getLogger(__name__)

_CHATKIT_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
_chatkit_cleanup_task: dict[str, asyncio.Task | None] = {"task": None}
_chatkit_server_ref: dict[str, OrcheoChatKitServer | None] = {"server": None}


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if value is None:
        return default
    try:
        return int(str(value))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return default


def chatkit_retention_days() -> int:
    from orcheo_backend.app import get_settings

    settings = get_settings()
    days = _coerce_int(settings.get("CHATKIT_RETENTION_DAYS", 30), 30)
    return days if days > 0 else 30


def get_chatkit_store() -> PostgresChatKitStore | None:
    server = _chatkit_server_ref.get("server")
    if server is None:
        return None
    store = getattr(server, "store", None)
    if isinstance(store, PostgresChatKitStore):
        return store
    return None


async def ensure_chatkit_cleanup_task() -> None:
    """Start the ChatKit cleanup background task if a store is configured."""
    if _chatkit_cleanup_task["task"] is not None:
        return

    from orcheo_backend.app import (
        _CHATKIT_CLEANUP_INTERVAL_SECONDS,
        _get_chatkit_store,
    )
    from orcheo_backend.app import (
        _chatkit_retention_days as retention_days_func,
    )

    store = _get_chatkit_store()
    if store is None:
        return

    retention_days = retention_days_func()
    interval_seconds = max(_CHATKIT_CLEANUP_INTERVAL_SECONDS, 300)

    async def _cleanup_loop() -> None:
        try:
            while True:
                cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
                try:
                    pruned = await store.prune_threads_older_than(cutoff)
                    if pruned:
                        logger.info(
                            "Pruned %s ChatKit thread(s) older than %s",
                            pruned,
                            cutoff.isoformat(),
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:  # pragma: no cover
                    logger.exception("ChatKit cleanup task failed")

                await asyncio.sleep(interval_seconds)
        finally:
            _chatkit_cleanup_task["task"] = None

    _chatkit_cleanup_task["task"] = asyncio.create_task(
        _cleanup_loop(),
        name="chatkit_cleanup",
    )


async def cancel_chatkit_cleanup_task() -> None:
    """Cancel the ChatKit cleanup task if it is running."""
    task = _chatkit_cleanup_task.get("task")
    if task is None:
        return

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    _chatkit_cleanup_task["task"] = None


def resolve_chatkit_token_issuer() -> ChatKitSessionTokenIssuer:
    """Return the configured ChatKit token issuer or raise a 503 error."""
    try:
        return get_chatkit_token_issuer()
    except ChatKitTokenConfigurationError as exc:
        detail = {
            "message": "ChatKit session token signing key is not configured",
            "code": "chatkit.signing_key_missing",
        }
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail,
        ) from exc


def get_chatkit_server() -> OrcheoChatKitServer:
    """Return the singleton ChatKit server wired to the repository."""
    server = _chatkit_server_ref["server"]
    if server is None:
        repository = get_repository()
        server = create_chatkit_server(repository, get_vault)
        _chatkit_server_ref["server"] = server
    return server


def sensitive_logging_enabled() -> bool:
    """Return True when sensitive debug logging should be enabled."""
    dev_environments = {"development", "dev", "local"}
    current_env = os.getenv("ORCHEO_ENV") or os.getenv("NODE_ENV", "production")
    if (current_env or "").lower() in dev_environments:
        return True
    return os.getenv("ORCHEO_LOG_SENSITIVE_DEBUG") == "1"


__all__ = [
    "cancel_chatkit_cleanup_task",
    "ensure_chatkit_cleanup_task",
    "get_chatkit_server",
    "resolve_chatkit_token_issuer",
    "sensitive_logging_enabled",
]
