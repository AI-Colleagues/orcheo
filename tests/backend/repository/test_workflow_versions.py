from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.models import WorkflowDraftAccess
from orcheo_backend.app.repository import (
    VersionDiff,
    WorkflowNotFoundError,
    WorkflowRepository,
    WorkflowVersionNotFoundError,
)
from .helpers import _remove_version


@pytest.mark.asyncio()
async def test_version_management(repository: WorkflowRepository) -> None:
    """Version CRUD supports numbering, listing, and retrieval."""

    workflow = await repository.create_workflow(
        name="Versioned",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )

    first = await repository.create_version(
        workflow.id,
        graph={"nodes": ["a"], "edges": []},
        metadata={"first": True},
        notes=None,
        created_by="author",
    )
    second = await repository.create_version(
        workflow.id,
        graph={"nodes": ["a", "b"], "edges": []},
        metadata={"first": False},
        notes="update",
        created_by="author",
    )

    versions = await repository.list_versions(workflow.id)
    assert [version.version for version in versions] == [1, 2]
    assert versions[0].id == first.id

    looked_up = await repository.get_version_by_number(workflow.id, 2)
    assert looked_up.id == second.id

    fetched = await repository.get_version(second.id)
    assert fetched.id == second.id

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.get_version_by_number(workflow.id, 3)

    with pytest.raises(WorkflowNotFoundError):
        await repository.get_version_by_number(uuid4(), 1)

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.get_version(uuid4())

    diff = await repository.diff_versions(workflow.id, 1, 2)
    assert isinstance(diff, VersionDiff)
    assert diff.base_version == 1
    assert diff.target_version == 2
    assert any('+    "b"' in line for line in diff.diff)


@pytest.mark.asyncio()
async def test_create_version_without_workflow(
    repository: WorkflowRepository,
) -> None:
    """Creating a version for an unknown workflow fails."""

    with pytest.raises(WorkflowNotFoundError):
        await repository.create_version(
            uuid4(),
            graph={},
            metadata={},
            notes=None,
            created_by="actor",
        )


@pytest.mark.asyncio()
async def test_get_latest_version_validation(
    repository: WorkflowRepository,
) -> None:
    """Latest version retrieval enforces workflow and version existence."""

    workflow = await repository.create_workflow(
        name="Latest",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="tester",
    )

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.get_latest_version(workflow.id)

    with pytest.raises(WorkflowNotFoundError):
        await repository.get_latest_version(uuid4())

    version = await repository.create_version(
        workflow.id,
        graph={"nodes": []},
        metadata={},
        notes=None,
        created_by="tester",
    )
    latest = await repository.get_latest_version(workflow.id)
    assert latest.id == version.id
    await _remove_version(repository, version.id)

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.get_latest_version(workflow.id)


@pytest.mark.asyncio()
async def test_update_version_runnable_config_finds_requested_version(
    repository: WorkflowRepository,
) -> None:
    """Runnable config updates target the requested version number."""

    workflow = await repository.create_workflow(
        name="Runnable Config",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph={"nodes": ["v1"]},
        metadata={},
        notes=None,
        created_by="author",
    )
    await repository.create_version(
        workflow.id,
        graph={"nodes": ["v2"]},
        metadata={},
        notes=None,
        created_by="author",
    )

    updated = await repository.update_version_runnable_config(
        workflow.id,
        version_number=2,
        runnable_config={"configurable": {"thread_id": "thr-2"}},
        actor="editor",
    )

    assert updated.version == 2
    assert updated.runnable_config == {"configurable": {"thread_id": "thr-2"}}


@pytest.mark.asyncio()
async def test_update_version_runnable_config_validation_errors(
    repository: WorkflowRepository,
) -> None:
    """Updating runnable config validates workflow and version existence."""

    workflow = await repository.create_workflow(
        name="Runnable Config Validation",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph={"nodes": []},
        metadata={},
        notes=None,
        created_by="author",
    )

    with pytest.raises(WorkflowNotFoundError):
        await repository.update_version_runnable_config(
            uuid4(),
            version_number=1,
            runnable_config={"tags": ["missing"]},
            actor="editor",
        )

    with pytest.raises(WorkflowVersionNotFoundError):
        await repository.update_version_runnable_config(
            workflow.id,
            version_number=99,
            runnable_config={"tags": ["missing-version"]},
            actor="editor",
        )
