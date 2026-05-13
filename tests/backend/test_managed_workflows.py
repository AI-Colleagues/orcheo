"""Tests for backend-owned managed workflows."""

from __future__ import annotations
import pytest
from orcheo.models.workflow import WorkflowDraftAccess
from orcheo.workspace import Workspace
from orcheo_backend.app.managed_workflows import (
    MANAGED_VIBE_WORKFLOW_HANDLE,
    MANAGED_VIBE_WORKFLOW_NAME,
    ensure_managed_vibe_workflow,
)
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
)


@pytest.mark.asyncio
async def test_ensure_managed_vibe_workflow_creates_seed() -> None:
    """A missing managed workflow is created with one initial version."""

    repository = InMemoryWorkflowRepository()
    workspace = Workspace(slug="default", name="Default Workspace")

    workflow = await ensure_managed_vibe_workflow(repository, workspace)

    assert workflow.handle == MANAGED_VIBE_WORKFLOW_HANDLE
    assert workflow.name == MANAGED_VIBE_WORKFLOW_NAME
    assert workflow.is_archived is False
    assert workflow.draft_access is WorkflowDraftAccess.AUTHENTICATED
    assert "orcheo-vibe-agent" in workflow.tags
    assert "external-agent" in workflow.tags
    assert all(not tag.startswith("workspace:") for tag in workflow.tags)

    versions = await repository.list_versions(workflow.id)
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].created_by == "system"
    assert versions[0].runnable_config["configurable"]["working_directory"] == (
        "/workspace/agents/{{workspace_id}}"
    )


@pytest.mark.asyncio
async def test_ensure_managed_vibe_workflow_reuses_existing_handle() -> None:
    """An existing managed workflow is reused regardless of workspace scope."""

    repository = InMemoryWorkflowRepository()
    original_workspace = Workspace(slug="alpha", name="Alpha Workspace")
    current_workspace = Workspace(slug="beta", name="Beta Workspace")
    workflow = await repository.create_workflow(
        name="Custom Vibe",
        handle=MANAGED_VIBE_WORKFLOW_HANDLE,
        slug="custom-vibe",
        description="User custom description",
        tags=["external-agent"],
        draft_access=WorkflowDraftAccess.AUTHENTICATED,
        actor="tester",
        workspace_id=str(original_workspace.id),
    )

    reused = await ensure_managed_vibe_workflow(repository, current_workspace)

    assert reused.id == workflow.id
    assert reused.description == "User custom description"

    versions = await repository.list_versions(workflow.id)
    assert len(versions) == 1
    assert versions[0].version == 1


@pytest.mark.asyncio
async def test_ensure_managed_vibe_workflow_restores_archived_workflow() -> None:
    """An archived managed workflow is unarchived without losing edits."""

    repository = InMemoryWorkflowRepository()
    workspace = Workspace(slug="default", name="Default Workspace")
    workflow = await repository.create_workflow(
        name="Custom Vibe",
        handle=MANAGED_VIBE_WORKFLOW_HANDLE,
        slug="custom-vibe",
        description="User custom description",
        tags=[f"workspace:{workspace.slug}", "external-agent"],
        draft_access=WorkflowDraftAccess.WORKSPACE,
        actor="tester",
        workspace_id=str(workspace.id),
    )
    archived = await repository.archive_workflow(workflow.id, actor="tester")
    assert archived.is_archived is True

    restored = await ensure_managed_vibe_workflow(repository, workspace)

    assert restored.id == workflow.id
    assert restored.is_archived is False
    assert restored.description == "User custom description"

    versions = await repository.list_versions(workflow.id)
    assert len(versions) == 1
    assert versions[0].version == 1


@pytest.mark.asyncio
async def test_ensure_managed_vibe_workflow_recovers_from_handle_conflict() -> None:
    """A handle conflict should reuse the already-created workflow."""

    class ConflictRepository:
        def __init__(self) -> None:
            self.workflow = None
            self.resolve_calls = 0
            self.get_calls = 0
            self.update_calls = 0

        async def resolve_workflow_ref(self, handle: str, *, include_archived: bool):
            self.resolve_calls += 1
            if self.resolve_calls == 1:
                raise WorkflowNotFoundError(handle)
            return self.workflow.id

        async def get_workflow(self, workflow_id):
            self.get_calls += 1
            return self.workflow

        async def update_workflow(self, workflow_id, **kwargs):
            self.update_calls += 1
            self.workflow.is_archived = False
            return self.workflow

        async def create_workflow(self, **kwargs):
            raise WorkflowHandleConflictError(kwargs["handle"])

        async def list_versions(self, workflow_id):
            return []

        async def create_version(self, *args, **kwargs):
            return None

    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Custom Vibe",
        handle=MANAGED_VIBE_WORKFLOW_HANDLE,
        slug="custom-vibe",
        description="User custom description",
        tags=["external-agent"],
        draft_access=WorkflowDraftAccess.AUTHENTICATED,
        actor="tester",
    )
    archived = await repository.archive_workflow(workflow.id, actor="tester")

    conflict_repository = ConflictRepository()
    conflict_repository.workflow = archived

    restored = await ensure_managed_vibe_workflow(conflict_repository, None)

    assert restored.id == workflow.id
    assert conflict_repository.resolve_calls == 2
    assert conflict_repository.get_calls == 1
    assert conflict_repository.update_calls == 1
