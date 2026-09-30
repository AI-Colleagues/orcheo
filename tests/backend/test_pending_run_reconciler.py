"""Recovery behavior for runs left pending after broker outages."""

from __future__ import annotations
import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import pytest
from orcheo.models import WorkflowRun
from orcheo_backend.app import pending_run_reconciler
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.repository_postgres import PostgresWorkflowRepository


@pytest.mark.asyncio
async def test_reconciler_stops_publishing_when_broker_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed broker should not receive the rest of the claimed batch."""
    runs = [
        WorkflowRun(workflow_version_id=uuid4(), triggered_by="cron", input_payload={})
        for _ in range(3)
    ]
    repository = AsyncMock()
    repository.claim_stale_pending_runs.side_effect = [[runs[0]], [runs[1]]]
    attempted: list[WorkflowRun] = []

    def publish(run: WorkflowRun) -> bool:
        attempted.append(run)
        return len(attempted) == 1

    monkeypatch.setattr(pending_run_reconciler, "_enqueue_run_for_execution", publish)

    published = await pending_run_reconciler.reconcile_pending_runs(repository)

    assert published == 1
    assert attempted == runs[:2]
    assert repository.claim_stale_pending_runs.await_count == 2
    repository.claim_stale_pending_runs.assert_any_await(limit=1)
    repository.mark_run_enqueued.assert_awaited_once_with(runs[0].id)


@pytest.mark.asyncio
async def test_reconciler_first_publish_failure_claims_no_later_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker outage leaves later runs unstamped for the next pass."""
    run = WorkflowRun(
        workflow_version_id=uuid4(), triggered_by="cron", input_payload={}
    )
    repository = AsyncMock()
    repository.claim_stale_pending_runs.return_value = [run]
    monkeypatch.setattr(
        pending_run_reconciler, "_enqueue_run_for_execution", lambda _: False
    )

    assert await pending_run_reconciler.reconcile_pending_runs(repository) == 0
    repository.claim_stale_pending_runs.assert_awaited_once_with(limit=1)
    repository.mark_run_enqueued.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_skips_in_memory_and_inprocess_deployments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a Celery-backed PostgreSQL deployment starts the reconciler."""

    def fail_on_task(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Reconciliation task must not start")

    monkeypatch.setattr(pending_run_reconciler.asyncio, "create_task", fail_on_task)
    configurations = [
        (InMemoryWorkflowRepository(), False),
        (PostgresWorkflowRepository("postgresql://test"), True),
    ]
    for repository, inprocess in configurations:
        monkeypatch.setattr(
            pending_run_reconciler,
            "inprocess_execution_enabled",
            lambda: inprocess,
        )
        async with pending_run_reconciler.pending_run_reconciliation(repository):
            pass


@pytest.mark.asyncio
async def test_stale_active_runs_are_logged_for_operator_review(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Old quota slots become visible without an automatic status change."""
    oldest = datetime(2026, 9, 29, tzinfo=UTC)
    repository = AsyncMock()
    repository.list_stale_active_runs.return_value = [
        SimpleNamespace(
            workspace_id="workspace-1",
            status="running",
            count=2,
            oldest_updated_at=oldest,
        )
    ]

    with caplog.at_level(logging.WARNING):
        await pending_run_reconciler.log_stale_active_runs(repository)

    assert "workspace_id=workspace-1 status=running count=2" in caplog.text
    assert oldest.isoformat() in caplog.text
    repository.list_stale_active_runs.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_monitor_runs_even_when_reconciliation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed publish pass must not suppress the quota warning check."""
    monitor = AsyncMock()
    monkeypatch.setattr(
        pending_run_reconciler,
        "reconcile_pending_runs",
        AsyncMock(side_effect=RuntimeError("broker unavailable")),
    )
    monkeypatch.setattr(pending_run_reconciler, "log_stale_active_runs", monitor)
    monkeypatch.setattr(
        pending_run_reconciler.asyncio,
        "sleep",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    repository = AsyncMock()
    repository.fail_orphaned_worker_runs.return_value = []
    with pytest.raises(asyncio.CancelledError):
        await pending_run_reconciler.run_pending_reconciler(repository)

    monitor.assert_awaited_once()
    repository.fail_orphaned_worker_runs.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconciler_stops_when_no_pending_runs_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty claim must not publish or confirm any run."""
    repository = AsyncMock()
    repository.claim_stale_pending_runs.return_value = []
    publish = Mock()
    monkeypatch.setattr(pending_run_reconciler, "_enqueue_run_for_execution", publish)

    assert await pending_run_reconciler.reconcile_pending_runs(repository) == 0

    repository.claim_stale_pending_runs.assert_awaited_once_with(limit=1)
    repository.mark_run_enqueued.assert_not_awaited()
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_reconciler_bounds_each_pass_to_twenty_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backlog cannot keep a reconciliation pass running indefinitely."""
    runs = [
        WorkflowRun(workflow_version_id=uuid4(), triggered_by="cron", input_payload={})
        for _ in range(21)
    ]
    repository = AsyncMock()
    repository.claim_stale_pending_runs.side_effect = [[run] for run in runs]
    publish = Mock(return_value=True)
    monkeypatch.setattr(pending_run_reconciler, "_enqueue_run_for_execution", publish)

    assert await pending_run_reconciler.reconcile_pending_runs(repository) == 20

    assert [call.args[0] for call in publish.call_args_list] == runs[:20]
    assert [call.args[0] for call in repository.mark_run_enqueued.await_args_list] == [
        run.id for run in runs[:20]
    ]
    assert repository.claim_stale_pending_runs.await_count == 20


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cancel_during",
    ["reconcile_pending_runs", "fail_orphaned_worker_runs", "log_stale_active_runs"],
)
async def test_reconciler_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch, cancel_during: str
) -> None:
    """Cancellation during either database operation stops the loop immediately."""
    reconcile = AsyncMock()
    monitor = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr(pending_run_reconciler, "reconcile_pending_runs", reconcile)
    monkeypatch.setattr(pending_run_reconciler, "log_stale_active_runs", monitor)
    monkeypatch.setattr(pending_run_reconciler.asyncio, "sleep", sleep)
    repository = AsyncMock()
    repository.fail_orphaned_worker_runs.return_value = []
    operation = (
        repository.fail_orphaned_worker_runs
        if cancel_during == "fail_orphaned_worker_runs"
        else getattr(pending_run_reconciler, cancel_during)
    )
    operation.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await pending_run_reconciler.run_pending_reconciler(repository)

    reconcile.assert_awaited_once()
    assert monitor.await_count == (1 if cancel_during == "log_stale_active_runs" else 0)
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_fails", [False, True])
async def test_orphan_recovery_reports_results_and_preserves_monitoring(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    recovery_fails: bool,
) -> None:
    """Recovered runs and database errors are visible without stopping monitoring."""
    run = WorkflowRun(
        workflow_version_id=uuid4(), triggered_by="cron", workspace_id="workspace-1"
    )
    repository = AsyncMock()
    repository.fail_orphaned_worker_runs.return_value = [run]
    if recovery_fails:
        repository.fail_orphaned_worker_runs.side_effect = OSError("database offline")
    monitor = AsyncMock()
    monkeypatch.setattr(pending_run_reconciler, "reconcile_pending_runs", AsyncMock())
    monkeypatch.setattr(pending_run_reconciler, "log_stale_active_runs", monitor)
    monkeypatch.setattr(
        pending_run_reconciler.asyncio,
        "sleep",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await pending_run_reconciler.run_pending_reconciler(repository)

    repository.fail_orphaned_worker_runs.assert_awaited_once_with()
    monitor.assert_awaited_once_with(repository)
    if recovery_fails:
        assert "Orphaned worker run recovery failed" in caplog.text
        assert "database offline" in caplog.text
    else:
        assert (
            f"Failed orphaned worker run {run.id} in workspace workspace-1"
            in caplog.text
        )


@pytest.mark.asyncio
async def test_reconciler_continues_after_monitor_failure_and_checks_every_five_passes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Monitoring errors are logged without preventing subsequent recovery passes."""
    reconcile = AsyncMock()
    monitor = AsyncMock(side_effect=[RuntimeError("database unavailable"), None])
    sleep = AsyncMock(side_effect=[None] * 5 + [asyncio.CancelledError])
    monkeypatch.setattr(pending_run_reconciler, "reconcile_pending_runs", reconcile)
    monkeypatch.setattr(pending_run_reconciler, "log_stale_active_runs", monitor)
    monkeypatch.setattr(pending_run_reconciler.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await pending_run_reconciler.run_pending_reconciler(AsyncMock())

    assert reconcile.await_count == 6
    assert monitor.await_count == 2
    assert sleep.await_count == 6
    sleep.assert_awaited_with(60)
    assert "Stale active run monitoring failed" in caplog.text


@pytest.mark.asyncio
async def test_reconciliation_context_cancels_and_awaits_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend shutdown cleans up its PostgreSQL reconciler even after an error."""
    repository = PostgresWorkflowRepository("postgresql://test")
    started = asyncio.Event()
    stopped = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    async def reconcile(received: PostgresWorkflowRepository) -> None:
        assert received is repository
        task = asyncio.current_task()
        assert task is not None
        tasks.append(task)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(
        pending_run_reconciler, "inprocess_execution_enabled", lambda: False
    )
    monkeypatch.setattr(pending_run_reconciler, "run_pending_reconciler", reconcile)

    with pytest.raises(RuntimeError, match="backend shutdown"):
        async with pending_run_reconciler.pending_run_reconciliation(repository):
            await asyncio.wait_for(started.wait(), timeout=1)
            assert tasks[0].get_name() == "pending_run_reconciler"
            raise RuntimeError("backend shutdown")

    assert stopped.is_set()
    assert tasks[0].cancelled()
