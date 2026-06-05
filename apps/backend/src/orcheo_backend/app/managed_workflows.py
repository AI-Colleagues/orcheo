"""Helpers for backend-owned managed workflows."""

from __future__ import annotations
from orcheo.models import Workflow
from orcheo.workspace import Workspace
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowRepository,
)


MANAGED_VIBE_WORKFLOW_HANDLE = "orcheo-vibe-agent"


async def ensure_managed_vibe_workflow(
    repository: WorkflowRepository,
    workspace: Workspace | None,
) -> Workflow:
    """Return the Orcheo Vibe workflow if it exists; raise if not found.

    External agent workflows are not supported in production mode.
    Seeding is no longer performed automatically.
    """
    workspace_id = str(workspace.id) if workspace is not None else None
    try:
        workflow_id = await repository.resolve_workflow_ref(
            MANAGED_VIBE_WORKFLOW_HANDLE,
            include_archived=True,
            workspace_id=workspace_id,
        )
    except WorkflowNotFoundError:
        msg = "Managed vibe workflow is not supported in production mode."
        raise RuntimeError(msg)  # noqa: B904
    return await repository.get_workflow(workflow_id)
