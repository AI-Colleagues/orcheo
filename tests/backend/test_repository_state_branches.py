"""Cover missing branches in InMemoryRepositoryState (lines 165->171, 271->275, 272->271)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from orcheo.models.workflow import Workflow, WorkflowVersion
from orcheo_backend.app.repository.errors import WorkflowNotFoundError
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState


def _build_state() -> tuple[InMemoryRepositoryState, UUID, UUID]:
    state = InMemoryRepositoryState()
    workflow = Workflow(name="test-workflow")
    state._workflows[workflow.id] = workflow
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=1,
        graph={},
        metadata={},
        created_by="tester",
    )
    state._versions[version.id] = version
    return state, workflow.id, version.id


def test_create_run_locked_exception_without_workspace_reraises() -> None:
    """When workspace_id is None and run creation fails, exception propagates (165->171)."""
    state, workflow_id, version_id = _build_state()

    def _boom(*args, **kwargs) -> None:
        raise RuntimeError("track failed")

    state._trigger_layer.track_run = _boom  # type: ignore[method-assign]

    # workspace_id is None so the slot-release branch is NOT taken (165->171 False)
    with pytest.raises(RuntimeError, match="track failed"):
        state._create_run_locked(
            workflow_id=workflow_id,
            workflow_version_id=version_id,
            triggered_by="manual",
            input_payload={},
            actor="tester",
            workspace_id=None,
        )


@pytest.mark.asyncio
async def test_resolve_workflow_ref_no_archived_match_for_workspace() -> None:
    """For-loop exhaustion when no archived match passes workspace filter (271->275)."""
    state = InMemoryRepositoryState()

    # Create an archived workflow in workspace-A
    wf = Workflow(name="archived-wf", handle="shared-handle")
    wf.is_archived = True
    state._workflows[wf.id] = wf
    state._workflow_workspaces[wf.id] = "workspace-a"
    state._rebuild_handle_indexes_locked()

    # Resolve with workspace-B: archived list has the workflow but workspace
    # filter (272->271 False branch) skips it, and the for loop exhausts (271->275)
    with pytest.raises(WorkflowNotFoundError):
        await state.resolve_workflow_ref(
            "shared-handle",
            workspace_id="workspace-b",
        )


@pytest.mark.asyncio
async def test_resolve_workflow_ref_archived_list_skips_wrong_workspace() -> None:
    """Workspace mismatch in archived list skips entries (272->271 continue)."""
    state = InMemoryRepositoryState()

    # Create two archived workflows with the same handle in different workspaces
    wf_a = Workflow(name="A", handle="shared-handle")
    wf_a.is_archived = True
    wf_b = Workflow(name="B", handle="shared-handle")
    wf_b.is_archived = True

    state._workflows[wf_a.id] = wf_a
    state._workflows[wf_b.id] = wf_b
    state._workflow_workspaces[wf_a.id] = "workspace-a"
    state._workflow_workspaces[wf_b.id] = "workspace-b"
    state._rebuild_handle_indexes_locked()

    # Resolve for workspace-b: should skip wf_a (wrong workspace) and return wf_b
    # This exercises the 272->271 continue branch before returning wf_b
    resolved = await state.resolve_workflow_ref(
        "shared-handle",
        workspace_id="workspace-b",
    )

    assert resolved == wf_b.id
