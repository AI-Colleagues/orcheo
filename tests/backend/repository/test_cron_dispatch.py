from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
import pytest
from orcheo.models import WorkflowDraftAccess
from orcheo.triggers.cron import CronTriggerConfig
from orcheo_backend.app.repository import WorkflowRepository
from .helpers import _remove_version


@pytest.mark.asyncio()
async def test_cron_trigger_overlap_guard(
    repository: WorkflowRepository,
) -> None:
    """Cron scheduler skips scheduling when an active run exists."""

    workflow = await repository.create_workflow(
        name="Overlap Flow",
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

    first = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
    )
    assert len(first) == 1
    run_id = first[0].id

    skipped = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    )
    assert skipped == []

    await repository.mark_run_started(run_id, actor="cron")
    await repository.mark_run_succeeded(run_id, actor="cron", output=None)

    next_runs = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 2, 9, 0, tzinfo=UTC)
    )
    assert len(next_runs) == 1


@pytest.mark.asyncio()
async def test_dispatch_due_cron_runs_handles_edge_cases(
    repository: WorkflowRepository,
) -> None:
    """Cron dispatcher gracefully skips invalid and incomplete state entries."""

    naive_now = datetime(2025, 1, 1, 11, 0)

    repository._trigger_layer.configure_cron(  # noqa: SLF001
        uuid4(), CronTriggerConfig()
    )

    workflow_without_versions = await repository.create_workflow(
        name="No Versions",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    await repository.configure_cron_trigger(
        workflow_without_versions.id,
        CronTriggerConfig(expression="0 11 * * *", timezone="UTC"),
    )

    workflow_missing_version = await repository.create_workflow(
        name="Missing Version",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    orphaned_version = await repository.create_version(
        workflow_missing_version.id,
        graph={},
        metadata={},
        notes=None,
        created_by="owner",
    )
    await _remove_version(repository, orphaned_version.id)
    repository._trigger_layer.configure_cron(  # noqa: SLF001
        workflow_missing_version.id,
        CronTriggerConfig(expression="0 11 * * *", timezone="UTC"),
    )

    workflow_not_due = await repository.create_workflow(
        name="Not Due",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    await repository.create_version(
        workflow_not_due.id,
        graph={},
        metadata={},
        notes=None,
        created_by="owner",
    )
    repository._trigger_layer.configure_cron(  # noqa: SLF001
        workflow_not_due.id,
        CronTriggerConfig(expression="0 12 * * *", timezone="UTC"),
    )

    runs = await repository.dispatch_due_cron_runs(now=naive_now)
    assert runs == []

    await repository.create_version(
        workflow_without_versions.id,
        graph={},
        metadata={},
        notes=None,
        created_by="owner",
    )

    retried_runs = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 1, 11, 0, tzinfo=UTC)
    )
    assert len(retried_runs) == 1
    assert retried_runs[0].triggered_by == "cron"


@pytest.mark.asyncio()
async def test_dispatch_due_cron_runs_respects_overlap_without_creating_runs(
    repository: WorkflowRepository,
) -> None:
    """Cron dispatcher skips scheduling when overlap guard is active."""

    workflow = await repository.create_workflow(
        name="Guarded Cron",
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
        CronTriggerConfig(expression="0 7 * * *", timezone="UTC"),
    )
    active_run_id = uuid4()
    repository._trigger_layer.track_run(workflow.id, active_run_id)  # noqa: SLF001
    repository._trigger_layer.register_cron_run(active_run_id)  # noqa: SLF001

    runs = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 1, 7, 0, tzinfo=UTC)
    )
    assert runs == []
