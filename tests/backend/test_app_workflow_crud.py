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
from orcheo_backend.app.errors import WorkspaceQuotaExceededError
from orcheo_backend.app.repository import (
    CronTriggerNotFoundError,
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.routers import workflows as workflows_router
from orcheo_backend.app.schemas.workflows import (
    WorkflowCanvasPayload,
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
)


_MOCK_WORKSPACE = SimpleNamespace(
    workspace_id=uuid4(),
    user_id="test-user",
    slug="test-workspace",
    quotas=SimpleNamespace(
        max_credentials=1000,
        max_workflows=1000,
        max_storage_rows=1_000_000,
    ),
)


@pytest.fixture(autouse=True)
def _patch_workspace_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub workspace governance helpers for endpoint-level tests."""

    class _StubWorkspaceRepo:
        def get_workspace(self, workspace_id):  # noqa: ARG002
            return SimpleNamespace(
                id=_MOCK_WORKSPACE.workspace_id,
                slug="test-workspace",
            )

    async def _no_op_managed_workflow(repository, workspace_record):  # noqa: ARG001
        return Workflow(
            id=uuid4(),
            name="Managed",
            handle="orcheo-vibe-agent",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

    async def _no_op_quota(repository, workspace):  # noqa: ARG001
        return None

    monkeypatch.setattr(
        workflows_router, "get_workspace_repository", lambda: _StubWorkspaceRepo()
    )
    monkeypatch.setattr(
        workflows_router, "ensure_managed_vibe_workflow", _no_op_managed_workflow
    )
    monkeypatch.setattr(
        workflows_router, "ensure_workspace_workflow_quota", _no_op_quota
    )


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
            self, *, include_archived: bool = False, workspace_id=None
        ):
            del include_archived
            return [workflow1, workflow2]

        async def get_latest_version(self, workflow_id):
            del workflow_id
            raise WorkflowVersionNotFoundError("No versions")

        async def get_cron_trigger_config(self, workflow_id):
            del workflow_id
            raise CronTriggerNotFoundError("No cron trigger configured")

    result = await list_workflows(Repository(), _MOCK_WORKSPACE, include_archived=False)

    items_by_id = {item.id: item for item in result}
    assert workflow1.id in items_by_id
    assert workflow2.id in items_by_id
    assert items_by_id[workflow1.id].latest_version is None
    assert items_by_id[workflow2.id].latest_version is None
    assert items_by_id[workflow1.id].is_scheduled is False
    assert items_by_id[workflow2.id].is_scheduled is False


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
            self, *, include_archived: bool = False, workspace_id=None
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

    result = await list_workflows(Repository(), _MOCK_WORKSPACE, include_archived=False)

    result_ids = {item.id for item in result}
    assert workflow1.id in result_ids
    assert workflow2.id in result_ids


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
            workspace_id=None,
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

    result = await create_workflow(request, Repository(), _MOCK_WORKSPACE)

    assert result.id == workflow_id
    assert result.name == "Test Workflow"
    assert result.slug == "test-workflow"


@pytest.mark.asyncio()
async def test_resolve_workflow_id_rejects_workspace_mismatch() -> None:
    """_resolve_workflow_id should hide workflows from other workspaces."""

    workflow_id = uuid4()
    other_workspace = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived, workspace_id
            return workflow_id

        async def get_workflow(self, wf_id):
            del wf_id
            return Workflow(
                id=workflow_id,
                name="Test Workflow",
                slug="test-workflow",
                workspace_id=str(other_workspace),
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    with pytest.raises(HTTPException) as exc_info:
        await workflows_router._resolve_workflow_id(
            Repository(),
            "workflow-ref",
            workspace_id=str(uuid4()),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_load_workflow_for_request_rejects_workspace_mismatch() -> None:
    """_load_workflow_for_request should reject workflows from other workspaces."""

    workflow_id = uuid4()
    other_workspace = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived, workspace_id
            return workflow_id

        async def get_workflow(self, wf_id):
            del wf_id
            return Workflow(
                id=workflow_id,
                name="Test Workflow",
                slug="test-workflow",
                workspace_id=str(other_workspace),
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    with pytest.raises(HTTPException) as exc_info:
        await workflows_router._load_workflow_for_request(
            Repository(),
            "workflow-ref",
            workspace_id=str(uuid4()),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_create_workflow_translates_quota_exceeded() -> None:
    """Create workflow should surface quota failures as HTTP errors."""

    class Repository:
        async def create_workflow(self, **kwargs):
            del kwargs
            return Workflow(
                id=uuid4(),
                name="unused",
                slug="unused",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = WorkflowCreateRequest(name="Test Workflow", actor="admin")

    async def _raise_quota(*args, **kwargs):  # noqa: ARG001
        raise WorkspaceQuotaExceededError(
            "Workspace reached its workflow quota",
            code="workspace.quota.workflows",
            details={"limit": 1, "current": 1},
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(workflows_router, "ensure_workspace_workflow_quota", _raise_quota)
        with pytest.raises(HTTPException) as exc_info:
            await create_workflow(request, Repository(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "workspace.quota.workflows"


@pytest.mark.asyncio()
async def test_update_workflow_translates_missing_workflow() -> None:
    """Update workflow should convert repository not-found errors into HTTP 404s."""

    workflow_id = uuid4()

    class Repository:
        async def update_workflow(self, workflow_id, **kwargs):
            del workflow_id, kwargs
            raise WorkflowNotFoundError("missing")

    async def _fake_load(*args, **kwargs):  # noqa: ARG001
        return Workflow(
            id=workflow_id,
            name="Test Workflow",
            slug="test-workflow",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

    request = WorkflowUpdateRequest(name="Updated")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(workflows_router, "_load_workflow_for_request", _fake_load)
        with pytest.raises(HTTPException) as exc_info:
            await update_workflow(
                "workflow-ref",
                request,
                Repository(),
                _MOCK_WORKSPACE,
                policy=object(),
            )

    assert exc_info.value.status_code == 404


def test_select_primary_workspace_handles_single_and_multiple_ids() -> None:
    assert workflows_router._select_primary_workspace(frozenset({"one"})) == "one"
    assert workflows_router._select_primary_workspace(frozenset({"one", "two"})) is None


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
            workspace_id=None,
        ):
            del name, slug, description, tags, draft_access, actor, handle, workspace_id
            raise WorkflowHandleConflictError(
                "Workflow handle 'demo' is already in use."
            )

    request = WorkflowCreateRequest(
        name="Test Workflow",
        handle="demo",
        actor="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_workflow(request, Repository(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "workflow.handle.conflict"


@pytest.mark.asyncio()
async def test_get_workflow_returns_workflow() -> None:
    """Get workflow endpoint returns the requested workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, workspace_id=None):
            return Workflow(
                id=wf_id,
                name="Test Workflow",
                slug="test-workflow",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    result = await get_workflow(str(workflow_id), Repository(), _MOCK_WORKSPACE)

    assert result.id == workflow_id
    assert result.name == "Test Workflow"


@pytest.mark.asyncio()
async def test_get_workflow_not_found() -> None:
    """Get workflow endpoint raises 404 for missing workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, workspace_id=None):
            raise WorkflowNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow(str(workflow_id), Repository(), _MOCK_WORKSPACE)

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
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, workspace_id=None):
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

    result = await get_workflow_canvas(str(workflow_id), Repository(), _MOCK_WORKSPACE)

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
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, workspace_id=None):
            del workspace_id
            return Workflow(
                id=wf_id,
                name="Test Workflow",
                slug="test-workflow",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

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
        str(workflow_id), request, Repository(), _MOCK_WORKSPACE
    )

    assert result.id == workflow_id
    assert result.name == "Updated Workflow"


@pytest.mark.asyncio()
async def test_update_workflow_not_found() -> None:
    """Update workflow endpoint raises 404 for missing workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, workspace_id=None):
            del wf_id, workspace_id
            raise WorkflowNotFoundError("not found")

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
        await update_workflow(str(workflow_id), request, Repository(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_update_workflow_translates_handle_conflicts() -> None:
    """Update workflow endpoint raises 409 for duplicate handles."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, workspace_id=None):
            del workspace_id
            return Workflow(
                id=wf_id,
                name="Test Workflow",
                slug="test-workflow",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

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
        await update_workflow(str(workflow_id), request, Repository(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "workflow.handle.conflict"


@pytest.mark.asyncio()
async def test_archive_workflow_returns_archived() -> None:
    """Archive workflow endpoint returns the archived workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, workspace_id=None):
            del workspace_id
            return Workflow(
                id=wf_id,
                name="Test Workflow",
                slug="test-workflow",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

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
        str(workflow_id), Repository(), _MOCK_WORKSPACE, actor="admin"
    )

    assert result.id == workflow_id
    assert result.is_archived is True


@pytest.mark.asyncio()
async def test_archive_workflow_not_found() -> None:
    """Archive workflow endpoint raises 404 for missing workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, workspace_id=None):
            del wf_id, workspace_id
            raise WorkflowNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await archive_workflow(
            str(workflow_id), Repository(), _MOCK_WORKSPACE, actor="admin"
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_archive_workflow_blocks_managed_vibe_workflow() -> None:
    """Archive workflow endpoint rejects the managed Orcheo Vibe workflow."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_workflow(self, wf_id, *, workspace_id=None):
            return Workflow(
                id=wf_id,
                handle="orcheo-vibe-agent",
                name="Orcheo Vibe",
                slug="orcheo-vibe-agent",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

        async def archive_workflow(self, wf_id, actor):
            del wf_id, actor
            raise AssertionError("archive_workflow should not be called")

    with pytest.raises(HTTPException) as exc_info:
        await archive_workflow(
            str(workflow_id), Repository(), _MOCK_WORKSPACE, actor="admin"
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "workflow.delete.protected"
