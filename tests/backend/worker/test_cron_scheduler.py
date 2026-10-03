"""Independent lean scheduler lifecycle tests."""

from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import pytest
from orcheo.models import WorkflowRun
from orcheo_backend.app import local_execution
from orcheo_backend.app.repository_postgres._triggers import _enqueue_run_for_execution
from orcheo_backend.worker import cron_scheduler
from orcheo_backend.worker.tasks import dispatch_cron_triggers, execute_run


@pytest.mark.asyncio
async def test_standalone_scheduler_starts_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown releases the scheduler without using a workflow execution slot."""
    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "true")
    stop = asyncio.Event()
    service = Mock(start=AsyncMock(side_effect=stop.set), stop=AsyncMock())
    factory = Mock(return_value=service)
    repository = Mock()
    monkeypatch.setattr(cron_scheduler, "get_repository", lambda: repository)
    monkeypatch.setattr(cron_scheduler, "CronSchedulerService", factory)
    await cron_scheduler.run_scheduler(stop)
    factory.assert_called_once_with(repository=repository)
    service.start.assert_awaited_once()
    service.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_standalone_scheduler_publishes_after_importing_with_local_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A previously read execution flag cannot route scheduled runs locally."""
    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "true")
    assert local_execution.inprocess_execution_enabled()
    stop = asyncio.Event()
    run = WorkflowRun(workflow_version_id=uuid4(), triggered_by="cron")
    publish = Mock()
    monkeypatch.setattr(execute_run, "apply_async", publish)
    monkeypatch.setattr(local_execution, "_inside_celery_task", lambda: False)

    async def dispatch() -> list[WorkflowRun]:
        assert _enqueue_run_for_execution(run)
        stop.set()
        return [run]

    repository = Mock(dispatch_due_cron_runs=AsyncMock(side_effect=dispatch))
    monkeypatch.setattr(cron_scheduler, "get_repository", lambda: repository)

    await asyncio.wait_for(cron_scheduler.run_scheduler(stop), timeout=1)

    publish.assert_called_once_with(args=(str(run.id),), headers=None)
    assert not local_execution.inprocess_execution_enabled()


def test_old_queued_cron_tasks_do_not_compete_with_standalone_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upgrade leftovers cannot dispatch a schedule alongside its new owner."""
    monkeypatch.setenv("ORCHEO_CRON_DISPATCH_OWNER", "scheduler")
    assert dispatch_cron_triggers() == {"dispatched_runs": []}
