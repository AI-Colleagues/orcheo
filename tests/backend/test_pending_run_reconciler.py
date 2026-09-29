"""Recovery behavior for runs left pending after broker outages."""

from __future__ import annotations
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from orcheo.models import WorkflowRun
from orcheo_backend.app import pending_run_reconciler


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
    repository.claim_stale_pending_runs.return_value = runs
    attempted: list[WorkflowRun] = []

    def publish(run: WorkflowRun) -> bool:
        attempted.append(run)
        return len(attempted) == 1

    monkeypatch.setattr(pending_run_reconciler, "_enqueue_run_for_execution", publish)

    published = await pending_run_reconciler.reconcile_pending_runs(repository)

    assert published == 1
    assert attempted == runs[:2]
