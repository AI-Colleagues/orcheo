from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRepository,
    WorkflowRunNotFoundError,
    WorkflowVersionNotFoundError,
)


@pytest.mark.asyncio()
async def test_run_lifecycle(repository: WorkflowRepository) -> None:
    """Runs can transition through success, failure, and cancellation."""

    workflow = await repository.create_workflow(
        name="Runnable",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    version = await repository.create_version(
        workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="owner",
    )

    run = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="runner",
        input_payload={"payload": True},
    )
    started = await repository.mark_run_started(run.id, actor="runner")
    assert started.status == "running"
    succeeded = await repository.mark_run_succeeded(
        run.id, actor="runner", output={"result": "ok"}
    )
    assert succeeded.status == "succeeded"
    assert succeeded.output_payload == {"result": "ok"}

    failed_run = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="runner",
        input_payload={},
    )
    failed = await repository.mark_run_failed(
        failed_run.id, actor="runner", error="boom"
    )
    assert failed.status == "failed"
    assert failed.error == "boom"

    cancelled_run = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="runner",
        input_payload={},
    )
    cancelled = await repository.mark_run_cancelled(
        cancelled_run.id, actor="runner", reason="stop"
    )
    assert cancelled.status == "cancelled"
    assert cancelled.error == "stop"

    runs = await repository.list_runs_for_workflow(workflow.id)
    assert {run.status for run in runs} == {"succeeded", "failed", "cancelled"}

    limited_runs = await repository.list_runs_for_workflow(workflow.id, limit=1)
    assert len(limited_runs) == 1


@pytest.mark.asyncio()
async def test_run_error_paths(repository: WorkflowRepository) -> None:
    """All run error branches raise the correct exceptions."""

    missing_workflow_id = uuid4()
    with pytest.raises(WorkflowNotFoundError):
        await repository.create_run(
            missing_workflow_id,
            workflow_version_id=uuid4(),
            triggered_by="actor",
            input_payload={},
        )

    workflow = await repository.create_workflow(
        name="Run Errors",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    _ = await repository.create_version(
        workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="owner",
    )

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.create_run(
            workflow.id,
            workflow_version_id=uuid4(),
            triggered_by="actor",
            input_payload={},
        )

    other_workflow = await repository.create_workflow(
        name="Other",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    mismatched_version = await repository.create_version(
        other_workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="owner",
    )

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.create_run(
            workflow.id,
            workflow_version_id=mismatched_version.id,
            triggered_by="actor",
            input_payload={},
        )

    with pytest.raises(WorkflowRunNotFoundError):
        await repository.get_run(uuid4())

    with pytest.raises(WorkflowRunNotFoundError):
        await repository.mark_run_started(uuid4(), actor="actor")

    with pytest.raises(WorkflowRunNotFoundError):
        await repository.mark_run_succeeded(uuid4(), actor="actor", output=None)

    with pytest.raises(WorkflowRunNotFoundError):
        await repository.mark_run_failed(uuid4(), actor="actor", error="err")

    with pytest.raises(WorkflowRunNotFoundError):
        await repository.mark_run_cancelled(uuid4(), actor="actor", reason=None)


@pytest.mark.asyncio()
async def test_create_run_rejects_archived_workflow(
    repository: WorkflowRepository,
) -> None:
    """Archived workflows cannot create runs."""
    workflow = await repository.create_workflow(
        name="Archived",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    await repository.archive_workflow(workflow.id, actor="owner")

    with pytest.raises(WorkflowNotFoundError):
        await repository.create_run(
            workflow.id,
            workflow_version_id=uuid4(),
            triggered_by="runner",
            input_payload={},
        )


@pytest.mark.asyncio()
async def test_run_merges_version_runnable_config(
    repository: WorkflowRepository,
) -> None:
    """Runs merge stored version config with per-run overrides."""
    workflow = await repository.create_workflow(
        name="Runnable Config",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="owner",
    )
    version = await repository.create_version(
        workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="owner",
        runnable_config={
            "tags": ["stored"],
            "metadata": {"env": "prod", "team": "ops"},
        },
    )

    run_with_override = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="runner",
        input_payload={},
        runnable_config={"tags": ["run"], "metadata": {"env": "stage"}},
    )
    assert run_with_override.tags == ["run"]
    assert run_with_override.metadata == {"env": "stage", "team": "ops"}

    run_with_defaults = await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="runner",
        input_payload={},
    )
    assert run_with_defaults.tags == ["stored"]
    assert run_with_defaults.metadata == {"env": "prod", "team": "ops"}
