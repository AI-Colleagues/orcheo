"""Tests for workflow version retrieval and diff endpoints."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo.models.workflow import Workflow, WorkflowVersion
from orcheo_backend.app import (
    diff_workflow_versions,
    get_workflow_version,
    list_workflow_versions,
)
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.routers import workflows as workflow_router


_MOCK_WORKSPACE = SimpleNamespace(workspace_id=uuid4())


@pytest.fixture(autouse=True)
def _stub_load_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace `_load_workflow_for_request` so tests can stub Repository.resolve_workflow_ref alone."""

    async def _load(
        repository, workflow_ref, *, include_archived=True, workspace_id=None
    ):  # noqa: ARG001
        try:
            workflow_id = await repository.resolve_workflow_ref(
                workflow_ref,
                include_archived=include_archived,
                workspace_id=workspace_id,
            )
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Workflow(
            id=workflow_id,
            name="Stub",
            slug="stub",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr(workflow_router, "_load_workflow_for_request", _load)


@pytest.mark.asyncio()
async def test_list_workflow_versions_success() -> None:
    """List workflow versions endpoint returns versions."""

    workflow_id = uuid4()
    version1_id = uuid4()
    version2_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def list_versions(self, wf_id):
            return [
                WorkflowVersion(
                    id=version1_id,
                    workflow_id=wf_id,
                    version=1,
                    graph={},
                    created_by="admin",
                    created_at=datetime.now(tz=UTC),
                    updated_at=datetime.now(tz=UTC),
                ),
                WorkflowVersion(
                    id=version2_id,
                    workflow_id=wf_id,
                    version=2,
                    graph={},
                    created_by="admin",
                    created_at=datetime.now(tz=UTC),
                    updated_at=datetime.now(tz=UTC),
                ),
            ]

    result = await list_workflow_versions(
        str(workflow_id), Repository(), _MOCK_WORKSPACE
    )

    assert len(result) == 2
    assert result[0].id == version1_id
    assert result[1].id == version2_id
    assert isinstance(result[0].mermaid, str)
    assert isinstance(result[1].mermaid, str)


@pytest.mark.asyncio()
async def test_list_workflow_versions_not_found() -> None:
    """List workflow versions raises 404 for missing workflow."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            raise WorkflowNotFoundError("not found")

        async def list_versions(self, wf_id):
            del wf_id
            raise WorkflowNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await list_workflow_versions(str(workflow_id), Repository(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_workflow_version_success() -> None:
    """Get workflow version endpoint returns specific version."""

    workflow_id = uuid4()
    version_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_version_by_number(self, wf_id, version_number):
            return WorkflowVersion(
                id=version_id,
                workflow_id=wf_id,
                version=version_number,
                graph={},
                created_by="admin",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    result = await get_workflow_version(
        str(workflow_id), 1, Repository(), _MOCK_WORKSPACE
    )

    assert result.id == version_id
    assert result.version == 1
    assert isinstance(result.mermaid, str)


@pytest.mark.asyncio()
async def test_get_workflow_version_workflow_not_found() -> None:
    """Get workflow version raises 404 for missing workflow."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_version_by_number(self, wf_id, version_number):
            raise WorkflowNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_version(str(workflow_id), 1, Repository(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_workflow_version_version_not_found() -> None:
    """Get workflow version raises 404 for missing version."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_version_by_number(self, wf_id, version_number):
            raise WorkflowVersionNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_version(str(workflow_id), 1, Repository(), _MOCK_WORKSPACE)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_diff_workflow_versions_success() -> None:
    """Diff workflow versions endpoint returns diff."""

    workflow_id = uuid4()

    class Diff:
        base_version = 1
        target_version = 2
        diff = ["+ node1", "- node2"]

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def diff_versions(self, wf_id, base, target):
            return Diff()

    result = await diff_workflow_versions(
        str(workflow_id), 1, 2, Repository(), _MOCK_WORKSPACE
    )

    assert result.base_version == 1
    assert result.target_version == 2
    assert result.diff == ["+ node1", "- node2"]


@pytest.mark.asyncio()
async def test_list_workflow_versions_handles_mermaid_render_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List workflow versions falls back to null Mermaid if rendering fails."""

    workflow_id = uuid4()
    version_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def list_versions(self, wf_id):
            return [
                WorkflowVersion(
                    id=version_id,
                    workflow_id=wf_id,
                    version=1,
                    graph={},
                    created_by="admin",
                    created_at=datetime.now(tz=UTC),
                    updated_at=datetime.now(tz=UTC),
                ),
            ]

    def _raise_mermaid_failure(_graph: object) -> str:
        raise ValueError("boom")

    monkeypatch.setattr(workflow_router, "_mermaid_from_graph", _raise_mermaid_failure)

    result = await list_workflow_versions(
        str(workflow_id), Repository(), _MOCK_WORKSPACE
    )

    assert len(result) == 1
    assert result[0].mermaid is None


@pytest.mark.asyncio()
async def test_diff_workflow_versions_workflow_not_found() -> None:
    """Diff workflow versions raises 404 for missing workflow."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def diff_versions(self, wf_id, base, target):
            raise WorkflowNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await diff_workflow_versions(
            str(workflow_id), 1, 2, Repository(), _MOCK_WORKSPACE
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_diff_workflow_versions_version_not_found() -> None:
    """Diff workflow versions raises 404 for missing version."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def diff_versions(self, wf_id, base, target):
            raise WorkflowVersionNotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await diff_workflow_versions(
            str(workflow_id), 1, 2, Repository(), _MOCK_WORKSPACE
        )

    assert exc_info.value.status_code == 404


def test_attach_mermaid_non_mapping_graph_uses_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_attach_mermaid should fall back to renderer when graph is not a mapping."""

    version = WorkflowVersion(
        id=uuid4(),
        workflow_id=uuid4(),
        version=1,
        graph={},
        created_by="admin",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    object.__setattr__(version, "graph", "serialized-graph")
    monkeypatch.setattr(
        workflow_router,
        "_mermaid_from_graph",
        lambda _graph: "flowchart TD; A-->B",
    )

    result = workflow_router._attach_mermaid(version)

    assert result.mermaid == "flowchart TD; A-->B"
