"""Unit tests for in-process workflow run execution."""

from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from orcheo.models import WorkflowRun, WorkflowRunStatus
from orcheo_backend.app import local_execution


def _make_run(workspace_id: str | None = "workspace-1") -> WorkflowRun:
    return WorkflowRun(
        id=uuid4(),
        workspace_id=workspace_id,
        workflow_version_id=uuid4(),
        status=WorkflowRunStatus.PENDING,
        triggered_by="cron",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ("nonsense", True),
        ("false", False),
        ("0", False),
        ("OFF", False),
        ("no", False),
        ("true", True),
        (" YES ", True),
        ("on", True),
        ("1", True),
    ],
)
def test_inprocess_execution_enabled(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    """In-process execution is on unless the environment disables it."""
    if value is None:
        monkeypatch.delenv("ORCHEO_INPROCESS_EXECUTION", raising=False)
    else:
        monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", value)
    assert local_execution.inprocess_execution_enabled() is expected


@pytest.mark.asyncio
async def test_execute_run_inprocess_delegates_to_worker_pipeline() -> None:
    """Execution reuses the worker's async run pipeline."""
    run_id = str(uuid4())
    with patch(
        "orcheo_backend.worker.tasks.execute_run_async",
        new=AsyncMock(return_value={"status": "succeeded"}),
    ) as execute:
        result = await local_execution.execute_run_inprocess(run_id, "workspace-1")

    assert result == {"status": "succeeded"}
    execute.assert_awaited_once_with(run_id, "workspace-1")


def test_schedule_run_inprocess_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the flag turned off the caller is told to fall back to Celery."""
    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "false")
    assert local_execution.schedule_run_inprocess(_make_run()) is False


@pytest.mark.asyncio
async def test_schedule_run_inprocess_inside_celery_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Celery task publishes to the broker instead of using its own loop."""
    monkeypatch.delenv("ORCHEO_INPROCESS_EXECUTION", raising=False)
    monkeypatch.setattr(local_execution, "_inside_celery_task", lambda: True)
    assert local_execution.schedule_run_inprocess(_make_run()) is False


def test_inside_celery_task_is_false_outside_a_task() -> None:
    """Outside a Celery task the guard does not block in-process execution."""
    assert local_execution._inside_celery_task() is False


def test_inside_celery_task_detects_an_active_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A current Celery task is detected through celery's task state."""
    from celery import _state

    monkeypatch.setattr(_state, "get_current_task", lambda: object())
    assert local_execution._inside_celery_task() is True


def test_schedule_run_inprocess_without_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside an event loop the Celery path is used."""
    monkeypatch.delenv("ORCHEO_INPROCESS_EXECUTION", raising=False)
    assert local_execution.schedule_run_inprocess(_make_run()) is False


@pytest.mark.asyncio
async def test_schedule_run_inprocess_executes_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An enabled scheduler runs the workflow on the current event loop."""
    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "true")
    run = _make_run()
    executed: list[tuple[str, str | None]] = []

    async def _execute(run_id: str, workspace_id: str | None) -> dict[str, str]:
        executed.append((run_id, workspace_id))
        return {"status": "succeeded"}

    with patch.object(local_execution, "execute_run_inprocess", new=_execute):
        assert local_execution.schedule_run_inprocess(run) is True
        for _ in range(10):
            if executed:
                break
            await asyncio.sleep(0)

    assert executed == [(str(run.id), "workspace-1")]


@pytest.mark.asyncio
async def test_schedule_run_inprocess_logs_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashing run is logged instead of surfacing an unhandled task error."""
    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "true")
    run = _make_run(workspace_id=None)

    async def _execute(run_id: str, workspace_id: str | None) -> dict[str, str]:
        raise RuntimeError("boom")

    with (
        patch.object(local_execution, "execute_run_inprocess", new=_execute),
        patch.object(local_execution, "logger") as logger,
    ):
        assert local_execution.schedule_run_inprocess(run) is True
        for _ in range(10):
            if logger.exception.called:
                break
            await asyncio.sleep(0)

    logger.exception.assert_called_once()
    for _ in range(10):
        if not local_execution._inprocess_run_tasks:
            break
        await asyncio.sleep(0)
    assert not local_execution._inprocess_run_tasks


@pytest.mark.asyncio
async def test_enqueue_run_prefers_inprocess_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repository enqueue helper skips Celery when in-process is enabled."""
    from orcheo_backend.app.repository_postgres import _triggers

    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "true")
    run = _make_run()

    with (
        patch.object(local_execution, "execute_run_inprocess", new=AsyncMock()),
        patch("orcheo_backend.worker.tasks.execute_run") as celery_task,
    ):
        _triggers._enqueue_run_for_execution(run)
        await asyncio.sleep(0)

    celery_task.apply_async.assert_not_called()


def test_enqueue_run_falls_back_to_celery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a running event loop the Celery task is published as before."""
    from orcheo_backend.app.repository_postgres import _triggers

    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "true")
    run = _make_run()
    celery_task = MagicMock()

    with patch.dict(
        "sys.modules",
        {"orcheo_backend.worker.tasks": MagicMock(execute_run=celery_task)},
    ):
        _triggers._enqueue_run_for_execution(run)

    celery_task.apply_async.assert_called_once_with(
        args=(str(run.id),),
        headers={"workspace_id": "workspace-1"},
    )


@pytest.mark.asyncio
async def test_drain_without_pending_runs_is_a_noop() -> None:
    """Draining an idle backend does nothing."""
    assert not local_execution._inprocess_run_tasks
    await local_execution.drain_inprocess_runs()


@pytest.mark.asyncio
async def test_drain_awaits_runs_that_finish_in_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that completes during the grace period is left alone."""
    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "true")
    run = _make_run()
    finished = asyncio.Event()

    async def _execute(_run_id: str, _workspace_id: str | None) -> dict[str, str]:
        finished.set()
        return {"status": "succeeded"}

    repository = MagicMock(mark_run_failed=AsyncMock())
    with (
        patch.object(local_execution, "execute_run_inprocess", new=_execute),
        patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=repository
        ),
    ):
        assert local_execution.schedule_run_inprocess(run) is True
        await local_execution.drain_inprocess_runs(timeout=5)

    assert finished.is_set()
    repository.mark_run_failed.assert_not_awaited()
    assert not local_execution._inprocess_run_tasks


@pytest.mark.asyncio
async def test_drain_fails_runs_that_outlast_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run still going at shutdown is cancelled and marked failed.

    Left `running` in the database it would read as an active cron run
    forever, and the default non-overlapping schedule would never fire again.
    """
    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "true")
    run = _make_run()
    started = asyncio.Event()

    async def _execute(_run_id: str, _workspace_id: str | None) -> dict[str, str]:
        started.set()
        await asyncio.sleep(60)
        return {"status": "succeeded"}  # pragma: no cover - cancelled first

    repository = MagicMock(mark_run_failed=AsyncMock())
    with (
        patch.object(local_execution, "execute_run_inprocess", new=_execute),
        patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=repository
        ),
    ):
        assert local_execution.schedule_run_inprocess(run) is True
        await started.wait()
        await local_execution.drain_inprocess_runs(timeout=0.01)

    repository.mark_run_failed.assert_awaited_once()
    assert repository.mark_run_failed.await_args.args == (run.id,)
    assert repository.mark_run_failed.await_args.kwargs["actor"] == "worker"
    assert not local_execution._inprocess_run_tasks


@pytest.mark.asyncio
async def test_drain_survives_a_repository_that_cannot_record_the_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown is not derailed when the run cannot be marked failed."""
    monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "true")
    run = _make_run()
    started = asyncio.Event()

    async def _execute(_run_id: str, _workspace_id: str | None) -> dict[str, str]:
        started.set()
        await asyncio.sleep(60)
        return {"status": "succeeded"}  # pragma: no cover - cancelled first

    repository = MagicMock(mark_run_failed=AsyncMock(side_effect=ValueError("gone")))
    with (
        patch.object(local_execution, "execute_run_inprocess", new=_execute),
        patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=repository
        ),
    ):
        assert local_execution.schedule_run_inprocess(run) is True
        await started.wait()
        await local_execution.drain_inprocess_runs(timeout=0.01)

    repository.mark_run_failed.assert_awaited_once()
    assert not local_execution._inprocess_run_tasks
