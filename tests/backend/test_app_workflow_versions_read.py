"""Tests for workflow version retrieval and diff endpoints."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import Workflow, WorkflowVersion
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
_MERMAID_SCRIPT = (
    "from langgraph.graph import END, START, StateGraph\n"
    "\n"
    "def build_graph():\n"
    "    graph = StateGraph(dict)\n"
    "    graph.add_node('fetch', lambda state: state)\n"
    "    graph.add_edge(START, 'fetch')\n"
    "    graph.add_edge('fetch', END)\n"
    "    return graph\n"
)


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
                    graph={
                        "format": "langgraph-script",
                        "source": _MERMAID_SCRIPT,
                        "entrypoint": "build_graph",
                    },
                    created_by="admin",
                    created_at=datetime.now(tz=UTC),
                    updated_at=datetime.now(tz=UTC),
                ),
                WorkflowVersion(
                    id=version2_id,
                    workflow_id=wf_id,
                    version=2,
                    graph={
                        "format": "langgraph-script",
                        "source": _MERMAID_SCRIPT,
                        "entrypoint": "build_graph",
                    },
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
    assert "fetch" in result[0].mermaid


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
                graph={
                    "format": "langgraph-script",
                    "source": _MERMAID_SCRIPT,
                    "entrypoint": "build_graph",
                },
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
    assert "fetch" in result.mermaid


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
async def test_list_workflow_versions_handles_mermaid_render_failure() -> None:
    """List workflow versions leaves Mermaid unset when no script payload exists."""

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
    """_attach_mermaid should fall back to the renderer when graph is not a mapping."""

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
    called: dict[str, object] = {}

    def _render(graph_payload: object) -> str:
        called["graph"] = graph_payload
        return "flowchart TD; A-->B"

    monkeypatch.setattr(
        workflow_router,
        "render_mermaid_from_graph_payload",
        _render,
    )

    result = workflow_router._attach_mermaid(version)

    assert result.mermaid == "flowchart TD; A-->B"
    assert called["graph"] == "serialized-graph"


def test_attach_mermaid_prefers_precomputed_index_mermaid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_attach_mermaid should use graph.index.mermaid before regenerating."""

    version = WorkflowVersion(
        id=uuid4(),
        workflow_id=uuid4(),
        version=1,
        graph={
            "index": {"mermaid": "graph TD;\n\tA --> B;"},
            "summary": {
                "nodes": [{"name": "ignored", "type": "TaskNode"}],
                "edges": [],
                "conditional_edges": [],
            },
        },
        created_by="admin",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    monkeypatch.setattr(
        workflow_router,
        "render_mermaid_from_graph_payload",
        lambda _graph: pytest.fail("renderer should not be called"),
    )

    result = workflow_router._attach_mermaid(version)

    assert result.mermaid == "graph TD;\n\tA --> B;"
