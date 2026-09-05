"""In-process cron dispatch loop for single-process deployments.

Cron triggers can also be dispatched by Celery Beat, which requires Redis and a
separate scheduler process. Since neither is guaranteed to be running, the
backend polls for due cron triggers on its own event loop by default. Set
``ORCHEO_INPROCESS_CRON=false`` wherever Celery Beat runs, so a schedule is not
dispatched twice.

The loop is only safe in a single-process backend. Its lock is process-local
and ``dispatch_due_cron_runs`` reads ``last_dispatched_at``, creates the run,
and writes the timestamp back over separate transactions, so two backend
processes sharing a database can both see one occurrence as due and dispatch
it. Turn the flag off for multi-worker or replicated deployments and let Beat,
which is a singleton, own dispatch.
"""

from __future__ import annotations
import asyncio
import logging
import math
import os
from orcheo_backend.app.repository import WorkflowRepository


logger = logging.getLogger(__name__)

INPROCESS_CRON_ENV_VAR = "ORCHEO_INPROCESS_CRON"
CRON_DISPATCH_INTERVAL_ENV_VAR = "ORCHEO_CRON_DISPATCH_INTERVAL"
DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS = 60.0
DEFAULT_INPROCESS_CRON = True

_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_FALSY_VALUES = {"0", "false", "no", "off"}


def inprocess_cron_enabled() -> bool:
    """Return True when the backend should dispatch cron triggers itself."""
    value = os.getenv(INPROCESS_CRON_ENV_VAR)
    if value is None:
        return DEFAULT_INPROCESS_CRON
    normalized = value.strip().lower()
    if normalized in _TRUTHY_VALUES:
        return True
    if normalized in _FALSY_VALUES:
        return False
    return DEFAULT_INPROCESS_CRON


def cron_dispatch_interval_seconds() -> float:
    """Return the configured dispatch interval, falling back to the default."""
    raw = os.getenv(CRON_DISPATCH_INTERVAL_ENV_VAR)
    if raw is None:
        return DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS
    try:
        interval = float(raw)
    except ValueError:
        logger.warning(
            "Ignoring invalid %s=%r; using %ss",
            CRON_DISPATCH_INTERVAL_ENV_VAR,
            raw,
            DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS,
        )
        return DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS
    # NaN and infinity survive float() and both compare False against <= 0.
    # Either one reaches asyncio.wait_for, whose selector rejects it with a
    # TypeError raised inside the event loop itself, taking the process down.
    if not math.isfinite(interval) or interval <= 0:
        logger.warning(
            "Ignoring non-positive or non-finite %s=%r; using %ss",
            CRON_DISPATCH_INTERVAL_ENV_VAR,
            raw,
            DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS,
        )
        return DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS
    return interval


class CronSchedulerService:
    """Dispatch due cron triggers from inside the backend process."""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        interval_seconds: float | None = None,
    ) -> None:
        """Initialize the scheduler bound to one repository."""
        self._repository = repository
        self._interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else cron_dispatch_interval_seconds()
        )
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the dispatch loop."""
        if self._task is not None:
            return
        logger.info(
            "Starting in-process cron scheduler (interval=%ss)",
            self._interval_seconds,
        )
        self._stop_event.clear()
        self._task = asyncio.create_task(self._dispatch_loop(), name="cron-scheduler")

    async def stop(self) -> None:
        """Stop the dispatch loop."""
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("Stopped in-process cron scheduler")

    async def dispatch_once(self) -> int:
        """Dispatch all due cron runs once and return how many were created."""
        runs = await self._repository.dispatch_due_cron_runs()
        if runs:
            logger.info("Dispatched %d cron run(s)", len(runs))
        return len(runs)

    async def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("In-process cron dispatch failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_seconds,
                )
            except TimeoutError:
                continue


__all__ = [
    "CRON_DISPATCH_INTERVAL_ENV_VAR",
    "DEFAULT_CRON_DISPATCH_INTERVAL_SECONDS",
    "DEFAULT_INPROCESS_CRON",
    "INPROCESS_CRON_ENV_VAR",
    "CronSchedulerService",
    "cron_dispatch_interval_seconds",
    "inprocess_cron_enabled",
]
