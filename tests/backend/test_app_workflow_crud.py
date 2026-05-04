"""Tests for workflow CRUD endpoints in ``orcheo_backend.app``."""

from __future__ import annotations
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo.models.workflow import Workflow
from orcheo_backend.app import (
    archive_workflow,
    create_workflow,
    get_workflow,
    get_workflow_canvas,
    list_workflows,
    update_workflow,
)
from orcheo_backend.app.repository import (
    CronTriggerNotFoundError,
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.schemas.workflows import (
    WorkflowCanvasPayload,
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
)


_MOCK_TENANT = SimpleNamespace(tenant_id=uuid4())


@pytest.mark.asyncio()
async def test_list_workflows_returns_all() -> None:
    """List workflows endpoint returns all workflows."""
    workflow1 = Workflow(
        id=uuid4(),
        name="Workflow 1",
        slug="workflow-1",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    workflow2 = Workflow(
        id=uuid4(),
        name="Workflow 2",
        slug="workflow-2",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    class Repository:
        async def list_workflows(
            self, *, include_archived: bool = False, tenant_id=None
        ):
            del include_archived
            return [workflow1, workflow2]

        async def get_latest_version(self, workflow_id):
            del workflow_id
            raise WorkflowVersionNotFoundError("No versions")

        async def get_cron_trigger_config(self, workflow_id):
            del workflow_id
            raise CronTriggerNotFoundError("No cron trigger configured")

    result = await list_workflows(Repository(), _MOCK_TENANT, include_archived=False)

    assert len(result) == 2
    assert result[0].id == workflow1.id
    assert result[1].id == workflow2.id
    assert result[0].latest_version is None
    assert result[1].latest_version is None
    assert result[0].is_scheduled is False
    assert result[1].is_scheduled is False


@pytest.mark.asyncio()
async def test_list_workflows_fetches_metadata_concurrently() -> None:
    """List workflow metadata lookups should run concurrently across workflows."""
    workflow1 = Workflow(
        id=uuid4(),
        name="Workflow 1",
        slug="workflow-1",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    workflow2 = Workflow(
        id=uuid4(),
        name="Workflow 2",
        slug="workflow-2",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    all_latest_started = asyncio.Event()
    latest_started = 0

    class Repository:
        async def list_workflows(
            self, *, include_archived: bool = False, tenant_id=None
        ):
            del include_archived
            return [workflow1, workflow2]

        async def get_latest_version(self, workflow_id):
            del workflow_id
            nonlocal latest_started
            latest_started += 1
            if latest_started == 2:
                all_latest_started.set()
            await asyncio.wait_for(all_latest_started.wait(), timeout=0.5)
            raise WorkflowVersionNotFoundError("No versions")

        async def get_cron_trigger_config(self, workflow_id):
            del workflow_id
            raise CronTriggerNotFoundError("No cron trigger configured")

    result = await list_workflows(Repository(), _MOCK_TENANT, include_archived=False)

    assert len(result) == 2


@pytest.mark.asyncio()
async def test_create_workflow_returns_new_workflow() -> None:
    """Create workflow endpoint creates and returns new workflow."""
    workflow_id = uuid4()

    class Repository:
        async def create_workflow(
            self,
            *,
            name,
            slug=None,
            description=None,
            tags=None,
            draft_access=None,
            actor,
            tenant_id=None,
            handle=None,
        ):
            return Workflow(
                id=workflow_id,
                name=name,
                slug=slug,
                description=description,
                tags=tags,
                draft_access=draft_access,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = WorkflowCreateRequest(
        name="Test Workflow",
        slug="test-workflow",
        description="A test workflow",
        tags=["test"],
        actor="admin",
    )

    result = await create_workflow(request, Repository(), _MOCK_TENANT)

    assert result.id == workflow_id
    assert result.name == "Test Workflow"
    assert result.slug == "test-workflow"


@pytest.mark.asyncio()
async def test_create_workflow_translates_handle_conflicts() -> None:
    """Create workflow endpoint raises 409 for duplicate handles."""

    class Repository:
        async def create_workflow(
            self,
            *,
            name,
            slug=None,
            description=None,
            tags=None,
            draft_access=None,
            actor,
            handle=None,
            tenant_id=None,
        ):
            del name, slug, description, tags, draft_access, actor, handle, tenant_id
            raise WorkflowHandleConflictError(
                "Workflow handle 'demo' is already in use."
            )

    request = WorkflowCreateRequest(
        name="Test Workflow",
        handle="demo",
        actor="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_workflow(request, Repository(), _MOCK_TENANT)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "workflow.handle.conflict"


@pytest.mark.asyncio()
async def test_get_workflow_returns_workflow() -> None:
    """Get workflow endpoint returns the requested workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, tenant_id=None):
            return Workflow(
                id=wf_id,
                name="Test Workflow",
                slug="test-workflow",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    result = await get_workflow(str(workflow_id), Repository(), _MOCK_TENANT)

    assert result.id == workflow_id
    assert result.name == "Test Workflow"


@pytest.mark.asyncio()
async def test_get_workflow_not_found() -> None:
    """Get workflow endpoint raises 404 for missing workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, tenant_id=None):
            raise WorkflowNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow(str(workflow_id), Repository(), _MOCK_TENANT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_workflow_canvas_returns_compact_versions() -> None:
    """Canvas-open endpoint should avoid returning full version graphs."""
    workflow_id = uuid4()

    class Version:
        def __init__(self, version_number: int) -> None:
            now = datetime.now(tz=UTC)
            self.id = uuid4()
            self.workflow_id = workflow_id
            self.version = version_number
            self.graph = {"index": {"mermaid": f"graph TD; A-->B{version_number}"}}
            self.metadata = {"canvas": {"snapshot": {"nodes": [], "edges": []}}}
            self.runnable_config = {"run_name": f"v{version_number}"}
            self.notes = f"Version {version_number}"
            self.created_by = "tester"
            self.created_at = now
            self.updated_at = now

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, tenant_id=None):
            return Workflow(
                id=wf_id,
                name="Test Workflow",
                slug="test-workflow",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

        async def list_versions(self, wf_id):
            assert wf_id == workflow_id
            return [Version(1), Version(2)]

    result = await get_workflow_canvas(str(workflow_id), Repository(), _MOCK_TENANT)

    assert isinstance(result, WorkflowCanvasPayload)
    assert result.workflow.id == workflow_id
    assert [version.version for version in result.versions] == [1, 2]
    assert result.versions[0].mermaid == "graph TD; A-->B1"


@pytest.mark.asyncio()
async def test_update_workflow_returns_updated() -> None:
    """Update workflow endpoint returns the updated workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def update_workflow(
            self,
            wf_id,
            *,
            name,
            description,
            tags,
            draft_access,
            is_archived,
            actor,
            **kwargs,
        ):
            return Workflow(
                id=wf_id,
                name=name or "Test Workflow",
                slug="test-workflow",
                description=description,
                tags=tags or [],
                draft_access=draft_access or "personal",
                is_archived=is_archived,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = WorkflowUpdateRequest(
        name="Updated Workflow",
        description="Updated description",
        tags=["updated"],
        is_archived=False,
        actor="admin",
    )

    result = await update_workflow(
        str(workflow_id), request, Repository(), _MOCK_TENANT
    )

    assert result.id == workflow_id
    assert result.name == "Updated Workflow"


@pytest.mark.asyncio()
async def test_update_workflow_not_found() -> None:
    """Update workflow endpoint raises 404 for missing workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def update_workflow(
            self,
            wf_id,
            *,
            name,
            description,
            tags,
            draft_access,
            is_archived,
            actor,
            **kwargs,
        ):
            del draft_access
            raise WorkflowNotFoundError("not found")

    request = WorkflowUpdateRequest(
        name="Updated Workflow",
        actor="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_workflow(str(workflow_id), request, Repository(), _MOCK_TENANT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_update_workflow_translates_handle_conflicts() -> None:
    """Update workflow endpoint raises 409 for duplicate handles."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def update_workflow(
            self,
            wf_id,
            *,
            name,
            description,
            tags,
            draft_access,
            is_archived,
            actor,
            handle=None,
            **kwargs,
        ):
            del wf_id, name, description, tags, draft_access, is_archived, actor, handle
            raise WorkflowHandleConflictError(
                "Workflow handle 'demo' is already in use."
            )

    request = WorkflowUpdateRequest(
        handle="demo",
        actor="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_workflow(str(workflow_id), request, Repository(), _MOCK_TENANT)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "workflow.handle.conflict"


@pytest.mark.asyncio()
async def test_archive_workflow_returns_archived() -> None:
    """Archive workflow endpoint returns the archived workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def archive_workflow(self, wf_id, actor):
            return Workflow(
                id=wf_id,
                name="Test Workflow",
                slug="test-workflow",
                is_archived=True,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    result = await archive_workflow(
        str(workflow_id), Repository(), _MOCK_TENANT, actor="admin"
    )

    assert result.id == workflow_id
    assert result.is_archived is True


@pytest.mark.asyncio()
async def test_archive_workflow_not_found() -> None:
    """Archive workflow endpoint raises 404 for missing workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, tenant_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def archive_workflow(self, wf_id, actor):
            raise WorkflowNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await archive_workflow(
            str(workflow_id), Repository(), _MOCK_TENANT, actor="admin"
        )

    assert exc_info.value.status_code == 404
