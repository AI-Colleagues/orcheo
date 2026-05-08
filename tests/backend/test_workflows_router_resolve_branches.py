"""Cover missing branches in routers/workflows.py:
- WorkflowNotFoundError in _resolve_workflow_id (lines 277-278)
- workflow_workspace_id is None branch (lines 282->284)
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from orcheo.models.workflow import Workflow, WorkflowDraftAccess
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.repository.errors import WorkflowNotFoundError
from orcheo_backend.app.routers.workflows import _resolve_workflow_id


# ---------------------------------------------------------------------------
# _resolve_workflow_id: get_workflow raises WorkflowNotFoundError (lines 277-278)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_workflow_id_get_workflow_not_found_raises_404() -> None:
    """When get_workflow raises after resolve succeeds, raise 404."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(self, ref, *, include_archived=True):
            return workflow_id

        async def get_workflow(self, wid: UUID) -> Workflow:
            raise WorkflowNotFoundError(str(wid))

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_workflow_id(
            Repository(),  # type: ignore[arg-type]
            "some-ref",
            workspace_id="ws-1",
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Workflow not found"


# ---------------------------------------------------------------------------
# _resolve_workflow_id: workspace_id is None on workflow (lines 282->284)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_workflow_id_no_workspace_on_workflow_succeeds() -> None:
    """Workflow with no workspace_id passes workspace check (282->284 False branch)."""

    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Global Workflow",
        handle="global-wf",
        slug=None,
        description="No workspace assigned",
        tags=[],
        draft_access=WorkflowDraftAccess.AUTHENTICATED,
        actor="tester",
        workspace_id=None,  # no workspace
    )

    # Resolving with a workspace_id should succeed because workflow.workspace_id is None
    result = await _resolve_workflow_id(
        repository,  # type: ignore[arg-type]
        str(workflow.id),
        workspace_id="ws-any",
    )

    assert result == str(workflow.id)
