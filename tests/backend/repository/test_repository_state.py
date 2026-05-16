from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.repository import (
    RepositoryError,
    WorkflowNotFoundError,
    WorkflowRepository,
    WorkflowRunNotFoundError,
    WorkflowVersionNotFoundError,
)


@pytest.mark.asyncio()
async def test_reset_clears_internal_state(
    repository: WorkflowRepository,
) -> None:
    """Reset removes all previously stored workflows, versions, and runs."""

    workflow = await repository.create_workflow(
        name="Reset",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="actor",
    )
    version = await repository.create_version(
        workflow.id,
        graph={},
        metadata={},
        notes=None,
        created_by="actor",
    )
    await repository.create_run(
        workflow.id,
        workflow_version_id=version.id,
        triggered_by="actor",
        input_payload={},
    )

    await repository.reset()

    with pytest.raises(WorkflowNotFoundError):
        await repository.get_workflow(workflow.id)


def test_repository_error_hierarchy() -> None:
    """Ensure repository-specific errors inherit from the common base."""

    assert issubclass(WorkflowNotFoundError, RepositoryError)
    assert issubclass(WorkflowVersionNotFoundError, RepositoryError)
    assert issubclass(WorkflowRunNotFoundError, RepositoryError)


@pytest.mark.asyncio()
async def test_list_entities_error_paths(
    repository: WorkflowRepository,
) -> None:
    """Listing versions or runs for unknown workflows surfaces not found errors."""

    missing_id = uuid4()
    with pytest.raises(WorkflowNotFoundError):
        await repository.list_versions(missing_id)

    with pytest.raises(WorkflowNotFoundError):
        await repository.list_runs_for_workflow(missing_id)
