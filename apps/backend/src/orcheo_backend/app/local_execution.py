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
from orcheo.models import WorkflowRun


logger = logging.getLogger(__name__)

INPROCESS_EXECUTION_ENV_VAR = "ORCHEO_INPROCESS_EXECUTION"
DEFAULT_INPROCESS_EXECUTION = True

_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_FALSY_VALUES = {"0", "false", "no", "off"}

# Keep strong references so the event loop cannot garbage-collect a run
# mid-execution (asyncio only holds weak references to running tasks).
_inprocess_run_tasks: set[asyncio.Task[Any]] = set()


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
    _inprocess_run_tasks.add(task)
    task.add_done_callback(_inprocess_run_tasks.discard)
    logger.info("Scheduled run %s for in-process execution", run.id)
    return True


__all__ = [
    "DEFAULT_INPROCESS_EXECUTION",
    "INPROCESS_EXECUTION_ENV_VAR",
    "execute_run_inprocess",
    "inprocess_execution_enabled",
    "schedule_run_inprocess",
]
