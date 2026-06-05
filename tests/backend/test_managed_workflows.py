"""Tests for backend-owned managed workflows."""

from __future__ import annotations
import pytest
from orcheo.models import WorkflowDraftAccess
from orcheo.workspace import Workspace
from orcheo_backend.app.managed_workflows import (
    MANAGED_VIBE_WORKFLOW_HANDLE,
    ensure_managed_vibe_workflow,
)
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowNotFoundError,
)


@pytest.mark.asyncio
async def test_ensure_managed_vibe_workflow_raises_when_not_found() -> None:
    """A missing managed workflow raises RuntimeError in production mode."""

    repository = InMemoryWorkflowRepository()
    workspace = Workspace(slug="default", name="Default Workspace")

    with pytest.raises(RuntimeError, match="not supported in production mode"):
        await ensure_managed_vibe_workflow(repository, workspace)


@pytest.mark.asyncio
async def test_ensure_managed_vibe_workflow_returns_existing() -> None:
    """An existing managed workflow is returned without raising."""

    repository = InMemoryWorkflowRepository()
    workspace = Workspace(slug="default", name="Default Workspace")
    existing = await repository.create_workflow(
        name="Orcheo Vibe Agent",
        handle=MANAGED_VIBE_WORKFLOW_HANDLE,
        slug="orcheo-vibe-agent",
        description="Managed vibe workflow",
        tags=["orcheo-vibe-agent"],
        draft_access=WorkflowDraftAccess.AUTHENTICATED,
        actor="system",
        workspace_id=str(workspace.id),
    )

    result = await ensure_managed_vibe_workflow(repository, workspace)

    assert result.id == existing.id
    assert result.handle == MANAGED_VIBE_WORKFLOW_HANDLE


@pytest.mark.asyncio
async def test_ensure_managed_vibe_workflow_none_workspace() -> None:
    """Passing None as workspace raises RuntimeError for a missing workflow."""

    repository = InMemoryWorkflowRepository()

    with pytest.raises(RuntimeError, match="not supported in production mode"):
        await ensure_managed_vibe_workflow(repository, None)
