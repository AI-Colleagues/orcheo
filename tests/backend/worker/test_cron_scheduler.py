"""Independent lean scheduler lifecycle tests."""

from __future__ import annotations
import asyncio
from pathlib import Path
from unittest.mock import ANY, AsyncMock, Mock
from uuid import uuid4
import pytest
from orcheo.models import WorkflowRun
from orcheo_backend.app import local_execution
from orcheo_backend.app.repository_postgres._triggers import _enqueue_run_for_execution
from orcheo_backend.worker import cron_scheduler
from orcheo_backend.worker.tasks import dispatch_cron_triggers, execute_run


@pytest.fixture(autouse=True)
def isolate_heartbeat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ORCHEO_CRON_HEALTH_FILE", str(tmp_path / "heartbeat"))


@pytest.mark.asyncio
async def test_standalone_scheduler_starts_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown releases the scheduler without using a workflow execution slot."""
    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "true")
    stop = asyncio.Event()
    service = Mock(
        start=AsyncMock(side_effect=stop.set), stop=AsyncMock(), wait=AsyncMock()
    )
    factory = Mock(return_value=service)
    repository = Mock()
    monkeypatch.setattr(cron_scheduler, "get_repository", lambda: repository)
    monkeypatch.setattr(cron_scheduler, "CronSchedulerService", factory)
    await cron_scheduler.run_scheduler(stop)
    factory.assert_called_once_with(
        repository=repository, interval_seconds=ANY, on_dispatch=ANY
    )
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


@pytest.mark.asyncio
async def test_scheduler_heartbeat_cleared_at_start_and_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = cron_scheduler.heartbeat_path()
    path.write_text("stale heartbeat")
    stop = asyncio.Event()

    async def dispatch() -> list[WorkflowRun]:
        assert not path.exists()
        stop.set()
        return []

    repository = Mock(dispatch_due_cron_runs=AsyncMock(side_effect=dispatch))
    monkeypatch.setattr(cron_scheduler, "get_repository", lambda: repository)
    record = Mock(wraps=cron_scheduler.record_heartbeat)
    monkeypatch.setattr(cron_scheduler, "record_heartbeat", record)
    await asyncio.wait_for(cron_scheduler.run_scheduler(stop), timeout=1)
    record.assert_called_once()
    assert not path.exists()


@pytest.mark.parametrize("error", [None, RuntimeError("loop crashed")])
@pytest.mark.asyncio
async def test_scheduler_exits_when_background_loop_stops(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None
) -> None:
    service = Mock(
        start=AsyncMock(), stop=AsyncMock(), wait=AsyncMock(side_effect=error)
    )
    monkeypatch.setattr(
        cron_scheduler, "CronSchedulerService", Mock(return_value=service)
    )
    monkeypatch.setattr(cron_scheduler, "get_repository", Mock())
    with pytest.raises(RuntimeError, match="loop crashed|stopped unexpectedly"):
        await asyncio.wait_for(cron_scheduler.run_scheduler(asyncio.Event()), timeout=1)
    service.stop.assert_awaited_once()
    assert not cron_scheduler.heartbeat_path().exists()
