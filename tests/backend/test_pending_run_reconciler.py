"""Recovery behavior for runs left pending after broker outages."""

from __future__ import annotations
from unittest.mock import AsyncMock
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
