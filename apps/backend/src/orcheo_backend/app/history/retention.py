"""Background expiry of workflow traces (execution histories)."""

from __future__ import annotations
import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from orcheo.config import get_settings
from orcheo.config.defaults import _DEFAULTS
from orcheo_backend.app.dependencies import _history_store_ref
from orcheo_backend.app.history.postgres_store import PostgresRunHistoryStore


logger = logging.getLogger(__name__)

# The first sweep is deferred so startup stays free of history-store traffic.
TRACE_RETENTION_INITIAL_DELAY_SECONDS = 5 * 60
TRACE_RETENTION_INTERVAL_SECONDS = 6 * 60 * 60
_trace_retention_task: dict[str, asyncio.Task | None] = {"task": None}


def trace_retention_days() -> int:
    """Return the configured trace retention window in days (``0`` disables)."""
    default = int(str(_DEFAULTS["TRACE_RETENTION_DAYS"]))
    value = get_settings().get("TRACE_RETENTION_DAYS", default)
    try:
        days = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(days, 0)


async def prune_expired_traces(
    store: PostgresRunHistoryStore, retention_days: int
) -> int:
    """Delete execution histories that finished more than ``retention_days`` ago."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
    pruned = await store.prune_histories_older_than(cutoff)
    if pruned:
        logger.info(
            "Pruned %s workflow trace(s) completed before %s",
            pruned,
            cutoff.isoformat(),
        )
    return pruned


async def ensure_trace_retention_task() -> None:
    """Start the trace expiry background task when retention is enabled."""
    if _trace_retention_task["task"] is not None:
        return

    store = _history_store_ref.get("store")
    if not isinstance(store, PostgresRunHistoryStore):
        return

    retention_days = trace_retention_days()
    if retention_days == 0:
        logger.info("Workflow trace expiry disabled (ORCHEO_TRACE_RETENTION_DAYS=0)")
        return

    async def _retention_loop() -> None:
        try:
            await asyncio.sleep(TRACE_RETENTION_INITIAL_DELAY_SECONDS)
            while True:
                try:
                    await prune_expired_traces(store, retention_days)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Workflow trace expiry task failed")
                await asyncio.sleep(TRACE_RETENTION_INTERVAL_SECONDS)
        finally:
            _trace_retention_task["task"] = None

    _trace_retention_task["task"] = asyncio.create_task(
        _retention_loop(),
        name="trace_retention",
    )


async def cancel_trace_retention_task() -> None:
    """Cancel the trace expiry task if it is running."""
    task = _trace_retention_task.get("task")
    if task is None:
        return

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    _trace_retention_task["task"] = None


__all__ = [
    "cancel_trace_retention_task",
    "ensure_trace_retention_task",
    "prune_expired_traces",
    "trace_retention_days",
]
