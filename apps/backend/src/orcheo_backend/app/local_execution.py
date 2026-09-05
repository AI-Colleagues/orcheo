"""In-process execution of workflow runs for single-process deployments.

The packaged desktop app runs the backend without Redis, a Celery worker, or
Celery Beat, so dispatched runs would otherwise stay ``pending`` forever. The
backend therefore executes dispatched runs on its own event loop by default.
Set ``ORCHEO_INPROCESS_EXECUTION=false`` wherever a Celery worker runs, so runs
are published to the broker instead.
"""

from __future__ import annotations
import asyncio
import logging
import os
from typing import Any
from uuid import UUID
from orcheo.models import WorkflowRun


logger = logging.getLogger(__name__)

INPROCESS_EXECUTION_ENV_VAR = "ORCHEO_INPROCESS_EXECUTION"
DEFAULT_INPROCESS_EXECUTION = True

_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_FALSY_VALUES = {"0", "false", "no", "off"}

DEFAULT_DRAIN_TIMEOUT_SECONDS = 30.0

# Keep strong references so the event loop cannot garbage-collect a run
# mid-execution (asyncio only holds weak references to running tasks), and so
# shutdown can find every run still in flight. Maps each task to the run it
# executes, which drain_inprocess_runs needs to mark a cancelled run failed.
_inprocess_run_tasks: dict[asyncio.Task[Any], UUID] = {}


def inprocess_execution_enabled() -> bool:
    """Return True when runs should execute inside the backend process."""
    value = os.getenv(INPROCESS_EXECUTION_ENV_VAR)
    if value is None:
        return DEFAULT_INPROCESS_EXECUTION
    normalized = value.strip().lower()
    if normalized in _TRUTHY_VALUES:
        return True
    if normalized in _FALSY_VALUES:
        return False
    return DEFAULT_INPROCESS_EXECUTION


async def execute_run_inprocess(
    run_id: str, workspace_id: str | None
) -> dict[str, Any]:
    """Execute one workflow run using the shared worker execution pipeline."""
    from orcheo_backend.worker.tasks import execute_run_async

    return await execute_run_async(run_id, workspace_id)


async def _run_and_log(run_id: str, workspace_id: str | None) -> None:
    try:
        result = await execute_run_inprocess(run_id, workspace_id)
    except Exception:
        logger.exception("In-process execution of run %s crashed", run_id)
        return
    logger.info("In-process execution of run %s finished: %s", run_id, result)


def _inside_celery_task() -> bool:
    """Return True when running inside a Celery task.

    A Celery task drives its own short-lived event loop (via
    ``run_until_complete``), so a run scheduled onto it would be abandoned as
    soon as the task returns. The worker must always publish to the broker.
    """
    try:
        from celery import _state
    except ImportError:  # pragma: no cover - celery is a hard dependency
        return False
    return _state.get_current_task() is not None


def schedule_run_inprocess(run: WorkflowRun) -> bool:
    """Schedule ``run`` on the running event loop.

    Returns:
        True when the run was scheduled, False when in-process execution is
        disabled, when called from a Celery task, or when no event loop is
        running, in which case the caller should fall back to Celery.
    """
    if not inprocess_execution_enabled():
        return False
    if _inside_celery_task():
        logger.debug(
            "Run %s dispatched from a Celery task; publishing to the broker",
            run.id,
        )
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug(
            "In-process execution requested for run %s without a running "
            "event loop; falling back to Celery",
            run.id,
        )
        return False

    task = loop.create_task(
        _run_and_log(str(run.id), run.workspace_id),
        name=f"inprocess-run-{run.id}",
    )
    _inprocess_run_tasks[task] = run.id
    task.add_done_callback(_inprocess_run_tasks.pop)
    logger.info("Scheduled run %s for in-process execution", run.id)
    return True


async def _fail_abandoned_run(run_id: UUID) -> None:
    """Record that a run was cut short by backend shutdown."""
    from orcheo_backend.app.dependencies import get_repository
    from orcheo_backend.worker.tasks import WORKER_ACTOR

    try:
        await get_repository().mark_run_failed(
            run_id,
            actor=WORKER_ACTOR,
            error="Run was cancelled because the backend shut down mid-execution.",
        )
    except Exception:
        logger.exception("Could not mark abandoned in-process run %s as failed", run_id)


async def drain_inprocess_runs(
    timeout: float = DEFAULT_DRAIN_TIMEOUT_SECONDS,
) -> None:
    """Settle in-flight in-process runs before the event loop goes away.

    Anything still running when the loop tears down is cancelled silently and
    its database row is left ``running`` forever. Cron reads those rows as
    active work, and the default schedule forbids overlap, so one stranded run
    permanently blocks its workflow. Give each run ``timeout`` seconds to
    finish, then cancel the rest and mark them failed so the next dispatch is
    not blocked.
    """
    pending = list(_inprocess_run_tasks.items())
    if not pending:
        return

    logger.info("Draining %d in-process run(s) before shutdown", len(pending))
    tasks = [task for task, _ in pending]
    _, unfinished = await asyncio.wait(tasks, timeout=timeout)
    if not unfinished:
        return

    abandoned = [run_id for task, run_id in pending if task in unfinished]
    logger.warning(
        "Cancelling %d in-process run(s) that did not finish within %ss",
        len(abandoned),
        timeout,
    )
    for task in unfinished:
        task.cancel()
    await asyncio.gather(*unfinished, return_exceptions=True)
    for run_id in abandoned:
        await _fail_abandoned_run(run_id)


__all__ = [
    "DEFAULT_DRAIN_TIMEOUT_SECONDS",
    "DEFAULT_INPROCESS_EXECUTION",
    "INPROCESS_EXECUTION_ENV_VAR",
    "drain_inprocess_runs",
    "execute_run_inprocess",
    "inprocess_execution_enabled",
    "schedule_run_inprocess",
]
