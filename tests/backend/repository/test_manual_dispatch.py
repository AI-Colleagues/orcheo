from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.models.workflow import WorkflowDraftAccess
from orcheo.triggers.manual import ManualDispatchItem, ManualDispatchRequest
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRepository,
    WorkflowVersionNotFoundError,
)


@pytest.mark.asyncio()
async def test_manual_dispatch_defaults_to_latest_version(
    repository: WorkflowRepository,
) -> None:
    """Manual dispatch without explicit version targets the latest one."""

    workflow = await repository.create_workflow(
        name="Manual Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    _ = await repository.create_version(
        workflow.id,
        graph={"nodes": ["start"], "edges": []},
        metadata={},
        notes=None,
        created_by="author",
    )
    second_version = await repository.create_version(
        workflow.id,
        graph={"nodes": ["start", "end"], "edges": []},
        metadata={},
        notes=None,
        created_by="author",
    )

    request = ManualDispatchRequest(
        workflow_id=workflow.id,
        actor="operator",
        runs=[ManualDispatchItem(input_payload={"foo": "bar"})],
    )

    runs = await repository.dispatch_manual_runs(request)
    assert len(runs) == 1
    run = runs[0]
    assert run.triggered_by == "manual"
    assert run.workflow_version_id == second_version.id
    assert run.input_payload == {"foo": "bar"}

    stored = await repository.get_run(run.id)
    assert stored.audit_log[0].actor == "operator"


@pytest.mark.asyncio()
async def test_manual_dispatch_supports_batch_runs(
    repository: WorkflowRepository,
) -> None:
    """Batch dispatch respects explicit version overrides and ordering."""

    workflow = await repository.create_workflow(
        name="Batch Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    first_version = await repository.create_version(
        workflow.id,
        graph={"nodes": ["start"], "edges": []},
        metadata={},
        notes=None,
        created_by="author",
    )
    second_version = await repository.create_version(
        workflow.id,
        graph={"nodes": ["start", "branch"], "edges": []},
        metadata={},
        notes=None,
        created_by="author",
    )

    request = ManualDispatchRequest(
        workflow_id=workflow.id,
        actor="batcher",
        runs=[
            ManualDispatchItem(
                workflow_version_id=first_version.id,
                input_payload={"step": 1},
            ),
            ManualDispatchItem(
                workflow_version_id=second_version.id,
                input_payload={"step": 2},
            ),
        ],
    )

    runs = await repository.dispatch_manual_runs(request)
    assert [run.triggered_by for run in runs] == ["manual_batch", "manual_batch"]
    assert [run.workflow_version_id for run in runs] == [
        first_version.id,
        second_version.id,
    ]
    assert [run.input_payload for run in runs] == [{"step": 1}, {"step": 2}]


@pytest.mark.asyncio()
async def test_manual_dispatch_rejects_unknown_versions(
    repository: WorkflowRepository,
) -> None:
    """Dispatch raises when referencing missing versions or workflows."""

    workflow = await repository.create_workflow(
        name="Error Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.dispatch_manual_runs(
            ManualDispatchRequest(
                workflow_id=workflow.id,
                actor="operator",
                runs=[ManualDispatchItem(workflow_version_id=uuid4())],
            )
        )

    with pytest.raises(WorkflowNotFoundError):
        await repository.dispatch_manual_runs(
            ManualDispatchRequest(
                workflow_id=uuid4(),
                actor="operator",
                runs=[ManualDispatchItem()],
            )
        )

    other_workflow = await repository.create_workflow(
        name="Foreign Versions",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    foreign_version = await repository.create_version(
        other_workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="author",
    )

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.dispatch_manual_runs(
            ManualDispatchRequest(
                workflow_id=workflow.id,
                actor="operator",
                runs=[
                    ManualDispatchItem(
                        workflow_version_id=foreign_version.id,
                    )
                ],
            )
        )
