"""Cover the non-archived branch in conflict recovery for managed workflows (282->293)."""

from __future__ import annotations

import pytest

from orcheo.models.workflow import WorkflowDraftAccess
from orcheo_backend.app.managed_workflows import (
    MANAGED_VIBE_WORKFLOW_HANDLE,
    ensure_managed_vibe_workflow,
)
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
)


@pytest.mark.asyncio
async def test_ensure_managed_vibe_workflow_conflict_with_non_archived_workflow() -> (
    None
):
    """When a handle conflict occurs and the found workflow is NOT archived, skip update."""

    repository = InMemoryWorkflowRepository()

    # Pre-create an active (not archived) workflow with the managed handle.
    existing = await repository.create_workflow(
        name="Custom Vibe",
        handle=MANAGED_VIBE_WORKFLOW_HANDLE,
        slug=None,
        description="Already active",
        tags=["external-agent"],
        draft_access=WorkflowDraftAccess.AUTHENTICATED,
        actor="tester",
    )
    assert existing.is_archived is False

    # Simulate a conflict repository: first resolve call raises WorkflowNotFoundError
    # (so the code tries create_workflow), create raises WorkflowHandleConflictError,
    # then the second resolve call returns the existing workflow id.
    class ConflictRepository:
        def __init__(self) -> None:
            self._repo = repository
            self._resolve_calls = 0
            self.update_calls = 0

        async def resolve_workflow_ref(self, handle: str, *, include_archived: bool):
            self._resolve_calls += 1
            if self._resolve_calls == 1:
                raise WorkflowNotFoundError(handle)
            return existing.id

        async def get_workflow(self, workflow_id):
            return await self._repo.get_workflow(workflow_id)

        async def update_workflow(self, workflow_id, **kwargs):
            self.update_calls += 1
            return await self._repo.update_workflow(workflow_id, **kwargs)

        async def create_workflow(self, **kwargs):
            raise WorkflowHandleConflictError(kwargs["handle"])

        async def list_versions(self, workflow_id):
            return await self._repo.list_versions(workflow_id)

        async def create_version(self, *args, **kwargs):
            return await self._repo.create_version(*args, **kwargs)

    conflict_repo = ConflictRepository()

    result = await ensure_managed_vibe_workflow(conflict_repo, None)

    # The non-archived workflow should be returned without calling update_workflow
    assert result.id == existing.id
    assert conflict_repo.update_calls == 0
