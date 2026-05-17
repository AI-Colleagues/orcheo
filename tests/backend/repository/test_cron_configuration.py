from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
import pytest
from orcheo.models import WorkflowDraftAccess
from orcheo.triggers.cron import CronTriggerConfig
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRepository,
)


@pytest.mark.asyncio()
async def test_create_run_with_cron_source_tracks_overlap(
    repository: WorkflowRepository,
) -> None:
    """Cron-sourced runs register overlap tracking even without stored state."""

    workflow = await repository.create_workflow(
        name="Cron Indexed",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="cron",
    )
    version = await repository.create_version(
        workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="cron",
    )

    run = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="cron",
        input_payload={},
    )
    assert repository._trigger_layer._cron_run_index[run.id] == workflow.id

    await repository.mark_run_started(run.id, actor="cron")
    await repository.mark_run_succeeded(run.id, actor="cron", output=None)
    assert run.id not in repository._trigger_layer._cron_run_index


@pytest.mark.asyncio()
async def test_cron_trigger_configuration_and_dispatch(
    repository: WorkflowRepository,
) -> None:
    """Cron trigger configuration is persisted and schedules runs."""

    workflow = await repository.create_workflow(
        name="Cron Flow",
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

    saved = await repository.configure_cron_trigger(
        workflow.id,
        CronTriggerConfig(expression="0 12 * * *", timezone="UTC"),
    )
    assert saved.expression == "0 12 * * *"

    runs = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    )
    assert len(runs) == 1
    run = runs[0]
    assert run.triggered_by == "cron"
    assert run.input_payload["scheduled_for"] == "2025-01-01T12:00:00+00:00"

    repeat = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    )
    assert repeat == []

    fetched = await repository.get_cron_trigger_config(workflow.id)
    assert fetched.expression == "0 12 * * *"


@pytest.mark.asyncio()
async def test_cron_trigger_deletion_removes_schedule(
    repository: WorkflowRepository,
) -> None:
    """Deleting a cron trigger stops future dispatches."""

    workflow = await repository.create_workflow(
        name="Cron Cleanup",
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
        CronTriggerConfig(expression="*/5 * * * *", timezone="UTC"),
    )

    await repository.delete_cron_trigger(workflow.id)

    runs = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    )
    assert runs == []


@pytest.mark.asyncio()
async def test_cron_trigger_requires_existing_workflow(
    repository: WorkflowRepository,
) -> None:
    """Cron trigger helpers raise when the workflow is unknown."""

    with pytest.raises(WorkflowNotFoundError):
        await repository.configure_cron_trigger(uuid4(), CronTriggerConfig())

    with pytest.raises(WorkflowNotFoundError):
        await repository.get_cron_trigger_config(uuid4())


@pytest.mark.asyncio()
async def test_cron_trigger_delete_requires_existing_workflow(
    repository: WorkflowRepository,
) -> None:
    """Deleting cron triggers raises when the workflow does not exist."""

    with pytest.raises(WorkflowNotFoundError):
        await repository.delete_cron_trigger(uuid4())


@pytest.mark.asyncio()
async def test_cron_trigger_timezone_alignment(
    repository: WorkflowRepository,
) -> None:
    """Cron scheduling honors the configured timezone when computing dispatches."""

    workflow = await repository.create_workflow(
        name="Timezone Flow",
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
        CronTriggerConfig(expression="0 9 * * *", timezone="America/Los_Angeles"),
    )

    runs = await repository.dispatch_due_cron_runs(
        now=datetime(2025, 1, 1, 17, 0, tzinfo=UTC)
    )
    assert len(runs) == 1
