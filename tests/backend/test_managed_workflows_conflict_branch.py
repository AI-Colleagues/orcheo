"""Tests for managed workflow lookup behaviour when the workflow exists."""

from __future__ import annotations

import pytest

from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.managed_workflows import (
    MANAGED_VIBE_WORKFLOW_HANDLE,
    ensure_managed_vibe_workflow,
)
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
)


@pytest.mark.asyncio
async def test_ensure_managed_vibe_workflow_found_without_workspace() -> None:
    """Lookup succeeds when the managed workflow exists (no workspace scoping)."""

    repository = InMemoryWorkflowRepository()
    existing = await repository.create_workflow(
        name="Custom Vibe",
        handle=MANAGED_VIBE_WORKFLOW_HANDLE,
        slug=None,
        description="Already active",
        tags=["orcheo-vibe-agent"],
        draft_access=WorkflowDraftAccess.AUTHENTICATED,
        actor="tester",
    )
    assert existing.is_archived is False

    # With None workspace_id, the lookup should find the existing workflow.
    result = await ensure_managed_vibe_workflow(repository, None)

    assert result.id == existing.id
    assert result.is_archived is False
