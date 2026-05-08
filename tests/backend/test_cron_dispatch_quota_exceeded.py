"""Cover the WorkspaceQuotaExceededError handler in dispatch_due_cron_runs (lines 176-182)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from orcheo.models.workflow import WorkflowDraftAccess
from orcheo.triggers.cron import CronTriggerConfig
from orcheo_backend.app.errors import WorkspaceQuotaExceededError
from orcheo_backend.app.repository import InMemoryWorkflowRepository


@pytest.mark.asyncio
async def test_dispatch_due_cron_runs_skips_on_quota_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WorkspaceQuotaExceededError during cron dispatch logs a warning and continues."""

    repository = InMemoryWorkflowRepository()

    workflow = await repository.create_workflow(
        name="Quota Cron",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    await repository.create_version(
        workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="owner",
    )
    await repository.configure_cron_trigger(
        workflow.id,
        CronTriggerConfig(expression="0 9 * * *", timezone="UTC"),
    )

    # Monkey-patch _create_run_locked to raise WorkspaceQuotaExceededError
    def _quota_fail(**kwargs):
        raise WorkspaceQuotaExceededError(
            "Quota exceeded",
            code="workspace.quota.concurrent_runs",
            details={"limit": 1, "current": 1},
        )

    monkeypatch.setattr(repository, "_create_run_locked", _quota_fail)

    runs = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
    )

    # Should return empty list because the run was skipped due to quota
    assert runs == []
