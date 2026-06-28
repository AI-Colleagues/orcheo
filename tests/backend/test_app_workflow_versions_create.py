"""Tests for ingesting workflow versions and config-only updates."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo.models import Workflow, WorkflowVersion
from orcheo_backend.app import (
    ingest_workflow_version,
    update_workflow_version_runnable_config,
)
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.routers import workflows as workflow_routes
from orcheo_backend.app.routers.workflows import get_workflow_version_mermaid
from orcheo_backend.app.schemas.workflows import (
    WorkflowVersionIngestRequest,
    WorkflowVersionRunnableConfigUpdateRequest,
)


_MOCK_WORKSPACE = SimpleNamespace(workspace_id=uuid4())


@pytest.fixture(autouse=True)
def _stub_load_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace `_load_workflow_for_request` so tests can stub Repository.resolve_workflow_ref alone."""
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "allow_client_uploads")

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

    monkeypatch.setattr(workflow_routes, "_load_workflow_for_request", _load)


@pytest.mark.asyncio()
async def test_ingest_workflow_version_success() -> None:
    """Ingest workflow version creates version from script."""

    workflow_id = uuid4()
    version_id = uuid4()
    captured_config: dict[str, object] | None = None

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, workflow_id):
            raise WorkflowVersionNotFoundError(str(workflow_id))

        async def create_version(
            self,
            wf_id,
            graph,
            metadata,
            notes,
            created_by,
            runnable_config=None,
        ):
            nonlocal captured_config
            captured_config = runnable_config
            return WorkflowVersion(
                id=version_id,
                workflow_id=wf_id,
                version=1,
                graph=graph,
                created_by=created_by,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    script_code = (
        "from langgraph.graph import StateGraph\n"
        "graph = StateGraph(dict)\n"
        "graph.add_node('test', lambda x: x)"
    )
    request = WorkflowVersionIngestRequest(
        script=script_code,
        entrypoint="graph",
        runnable_config={"tags": ["ingest"]},
        created_by="admin",
    )

    result = await ingest_workflow_version(
        str(workflow_id), request, Repository(), _MOCK_WORKSPACE
    )

    assert result.id == version_id
    assert captured_config == {"tags": ["ingest"]}


@pytest.mark.asyncio()
async def test_ingest_workflow_version_script_error() -> None:
    """Ingest workflow version handles script ingestion errors."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def create_version(
            self,
            wf_id,
            graph,
            metadata,
            notes,
            created_by,
            runnable_config=None,
        ):
            return WorkflowVersion(
                id=uuid4(),
                workflow_id=wf_id,
                version=1,
                graph=graph,
                created_by=created_by,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = WorkflowVersionIngestRequest(
        script="invalid python code {",
        entrypoint="graph",
        created_by="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await ingest_workflow_version(
            str(workflow_id), request, Repository(), _MOCK_WORKSPACE
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio()
async def test_ingest_workflow_version_rejects_missing_required_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Template ingest fails early when required plugins are unavailable."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def create_version(
            self,
            wf_id,
            graph,
            metadata,
            notes,
            created_by,
            runnable_config=None,
        ):
            del wf_id, graph, metadata, notes, created_by, runnable_config
            raise AssertionError("create_version should not be called")

    monkeypatch.setattr(
        workflow_routes,
        "missing_required_plugins",
        lambda required_plugins: list(required_plugins),
    )

    request = WorkflowVersionIngestRequest(
        script="from langgraph.graph import StateGraph\ngraph = StateGraph(dict)",
        entrypoint="graph",
        metadata={
            "template": {
                "requiredPlugins": ["orcheo-plugin-lark-listener"],
            }
        },
        created_by="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await ingest_workflow_version(
            str(workflow_id), request, Repository(), _MOCK_WORKSPACE
        )

    assert exc_info.value.status_code == 400
    assert "orcheo-plugin-lark-listener" in str(exc_info.value.detail)


@pytest.mark.asyncio()
async def test_ingest_workflow_version_not_found() -> None:
    """Ingest workflow version raises 404 for missing workflow."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_latest_version(self, workflow_id):
            raise WorkflowVersionNotFoundError(str(workflow_id))

        async def create_version(
            self,
            wf_id,
            graph,
            metadata,
            notes,
            created_by,
            runnable_config=None,
        ):
            raise WorkflowNotFoundError("not found")

    script_code = (
        "from langgraph.graph import StateGraph\n"
        "graph = StateGraph(dict)\n"
        "graph.add_node('test', lambda x: x)"
    )
    request = WorkflowVersionIngestRequest(
        script=script_code,
        entrypoint="graph",
        created_by="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await ingest_workflow_version(
            str(workflow_id), request, Repository(), _MOCK_WORKSPACE
        )

    assert exc_info.value.status_code == 404


_INGEST_SCRIPT = (
    "from langgraph.graph import StateGraph\n"
    "graph = StateGraph(dict)\n"
    "graph.add_node('test', lambda x: x)"
)


def _build_capturing_repository(workflow_id, captured):
    """Return a repository stub that records create_version arguments."""

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived, workspace_id
            return workflow_id

        async def get_latest_version(self, workflow_id):
            raise WorkflowVersionNotFoundError(str(workflow_id))

        async def create_version(
            self,
            wf_id,
            graph,
            metadata,
            notes,
            created_by,
            runnable_config=None,
        ):
            captured["metadata"] = metadata
            captured["runnable_config"] = runnable_config
            return WorkflowVersion(
                id=uuid4(),
                workflow_id=wf_id,
                version=1,
                graph=graph,
                created_by=created_by,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    return Repository()


@pytest.mark.asyncio()
async def test_ingest_workflow_version_lifts_inline_configurable_schema() -> None:
    """Inline schema annotations resolve to runtime values plus version metadata."""
    workflow_id = uuid4()
    captured: dict[str, object] = {}
    request = WorkflowVersionIngestRequest(
        script=_INGEST_SCRIPT,
        entrypoint="graph",
        runnable_config={
            "configurable": {
                "ai_model": {
                    "type": "string",
                    "enum": ["openai:gpt-4.1-mini", "openai:gpt-5.4-mini"],
                    "title": "Model",
                    "default": "openai:gpt-4.1-mini",
                }
            }
        },
        created_by="admin",
    )

    await ingest_workflow_version(
        str(workflow_id),
        request,
        _build_capturing_repository(workflow_id, captured),
        _MOCK_WORKSPACE,
    )

    assert captured["runnable_config"] == {
        "configurable": {"ai_model": "openai:gpt-4.1-mini"}
    }
    assert captured["metadata"]["configurable_schema"] == {
        "ai_model": {
            "type": "string",
            "enum": ["openai:gpt-4.1-mini", "openai:gpt-5.4-mini"],
            "title": "Model",
            "default": "openai:gpt-4.1-mini",
        }
    }


@pytest.mark.asyncio()
async def test_ingest_workflow_version_keeps_sibling_schema_over_inline() -> None:
    """A schema supplied via metadata wins over annotations inferred inline."""
    workflow_id = uuid4()
    captured: dict[str, object] = {}
    request = WorkflowVersionIngestRequest(
        script=_INGEST_SCRIPT,
        entrypoint="graph",
        runnable_config={
            "configurable": {"ai_model": {"type": "string", "default": "inline"}}
        },
        metadata={
            "configurable_schema": {
                "ai_model": {"type": "string", "default": "from-sibling"}
            }
        },
        created_by="admin",
    )

    await ingest_workflow_version(
        str(workflow_id),
        request,
        _build_capturing_repository(workflow_id, captured),
        _MOCK_WORKSPACE,
    )

    assert captured["runnable_config"] == {"configurable": {"ai_model": "inline"}}
    assert captured["metadata"]["configurable_schema"] == {
        "ai_model": {"type": "string", "default": "from-sibling"}
    }


@pytest.mark.asyncio()
async def test_ingest_workflow_version_leaves_plain_configurable_untouched() -> None:
    """A configurable mapping without annotations is stored unchanged."""
    workflow_id = uuid4()
    captured: dict[str, object] = {}
    request = WorkflowVersionIngestRequest(
        script=_INGEST_SCRIPT,
        entrypoint="graph",
        runnable_config={"configurable": {"ai_model": "openai:gpt-4.1-mini"}},
        created_by="admin",
    )

    await ingest_workflow_version(
        str(workflow_id),
        request,
        _build_capturing_repository(workflow_id, captured),
        _MOCK_WORKSPACE,
    )

    assert captured["runnable_config"] == {
        "configurable": {"ai_model": "openai:gpt-4.1-mini"}
    }
    assert "configurable_schema" not in captured["metadata"]


@pytest.mark.asyncio()
async def test_ingest_workflow_version_rejects_schema_without_runtime_default() -> None:
    """An inline schema with no resolvable runtime value raises a 400."""
    workflow_id = uuid4()
    captured: dict[str, object] = {}
    request = WorkflowVersionIngestRequest(
        script=_INGEST_SCRIPT,
        entrypoint="graph",
        runnable_config={"configurable": {"ai_model": {"type": "string", "enum": []}}},
        created_by="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await ingest_workflow_version(
            str(workflow_id),
            request,
            _build_capturing_repository(workflow_id, captured),
            _MOCK_WORKSPACE,
        )

    assert exc_info.value.status_code == 400
    assert "no runtime default" in str(exc_info.value.detail)


@pytest.mark.asyncio()
async def test_update_workflow_version_runnable_config_success() -> None:
    """Version runnable-config endpoint persists config-only updates."""

    workflow_id = uuid4()
    version_id = uuid4()
    captured_actor: str | None = None
    captured_version: int | None = None
    captured_config: dict[str, object] | None = None

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def update_version_runnable_config(
            self,
            wf_id,
            *,
            version_number,
            runnable_config,
            actor,
        ):
            nonlocal captured_actor, captured_version, captured_config
            captured_actor = actor
            captured_version = version_number
            captured_config = runnable_config
            return WorkflowVersion(
                id=version_id,
                workflow_id=wf_id,
                version=version_number,
                graph={
                    "format": "langgraph-script",
                    "source": (
                        "from langgraph.graph import StateGraph\ngraph=StateGraph(dict)"
                    ),
                },
                created_by="admin",
                runnable_config=runnable_config,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )

    request = WorkflowVersionRunnableConfigUpdateRequest(
        runnable_config={"tags": ["studio"], "run_name": "studio-save"},
        actor="studio-user",
    )

    result = await update_workflow_version_runnable_config(
        str(workflow_id),
        3,
        request,
        Repository(),
        _MOCK_WORKSPACE,
    )

    assert result.id == version_id
    assert captured_actor == "studio-user"
    assert captured_version == 3
    assert captured_config == {"tags": ["studio"], "run_name": "studio-save"}


@pytest.mark.asyncio()
async def test_update_workflow_version_runnable_config_missing_version() -> None:
    """Version runnable-config endpoint maps missing versions to 404."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def update_version_runnable_config(
            self,
            wf_id,
            *,
            version_number,
            runnable_config,
            actor,
        ):
            del wf_id, version_number, runnable_config, actor
            raise WorkflowVersionNotFoundError("v99")

    request = WorkflowVersionRunnableConfigUpdateRequest(
        runnable_config={"tags": ["x"]},
        actor="cli",
    )
    with pytest.raises(HTTPException) as exc_info:
        await update_workflow_version_runnable_config(
            str(workflow_id),
            99,
            request,
            Repository(),
            _MOCK_WORKSPACE,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_update_workflow_version_runnable_config_missing_workflow() -> None:
    """Version runnable-config endpoint maps missing workflows to 404."""

    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def update_version_runnable_config(
            self,
            wf_id,
            *,
            version_number,
            runnable_config,
            actor,
        ):
            del wf_id, version_number, runnable_config, actor
            raise WorkflowNotFoundError("wf-missing")

    request = WorkflowVersionRunnableConfigUpdateRequest(
        runnable_config={"tags": ["x"]},
        actor="cli",
    )
    with pytest.raises(HTTPException) as exc_info:
        await update_workflow_version_runnable_config(
            str(workflow_id),
            1,
            request,
            Repository(),
            _MOCK_WORKSPACE,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_ingest_workflow_version_blocked_in_managed_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed mode (default) blocks client-supplied workflow ingestion with 403."""
    monkeypatch.delenv("ORCHEO_WORKFLOW_TRUST_MODE", raising=False)

    request = WorkflowVersionIngestRequest(
        script="from langgraph.graph import StateGraph\ngraph = StateGraph(dict)",
        entrypoint="graph",
        created_by="admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        await ingest_workflow_version(
            str(uuid4()),
            request,
            object(),
            _MOCK_WORKSPACE,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "workflow.ingestion.disabled"  # type: ignore[index]


@pytest.mark.asyncio()
async def test_get_workflow_version_mermaid_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mermaid endpoint returns rendered diagram for script payloads."""
    workflow_id = uuid4()
    script = (
        "from langgraph.graph import END, START, StateGraph\n"
        "\n"
        "def build_graph():\n"
        "    graph = StateGraph(dict)\n"
        "    graph.add_node('fetch', lambda state: state)\n"
        "    graph.add_edge(START, 'fetch')\n"
        "    graph.add_edge('fetch', END)\n"
        "    return graph\n"
    )
    version = WorkflowVersion(
        id=uuid4(),
        workflow_id=workflow_id,
        version=1,
        graph={
            "format": "langgraph-script",
            "source": script,
            "entrypoint": "build_graph",
        },
        created_by="admin",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    from orcheo_backend.app.routers import workflows as workflow_router

    monkeypatch.setattr(
        workflow_router,
        "render_mermaid_from_graph_payload",
        lambda graph: "flowchart TD;\n\tfetch --> __end__;",
    )

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_version_by_number(self, wf_id, version_number):
            return version

    result = await get_workflow_version_mermaid(
        str(workflow_id), 1, Repository(), _MOCK_WORKSPACE
    )

    assert result == {"mermaid": "flowchart TD;\n\tfetch --> __end__;"}


@pytest.mark.asyncio()
async def test_get_workflow_version_mermaid_raises_for_non_declarative() -> None:
    """Mermaid endpoint returns 422 for non-declarative workflow versions."""
    workflow_id = uuid4()
    version = WorkflowVersion(
        id=uuid4(),
        workflow_id=workflow_id,
        version=1,
        graph={"format": "langgraph-script", "source": "graph = None"},
        created_by="admin",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            del workflow_ref, include_archived
            return workflow_id

        async def get_version_by_number(self, wf_id, version_number):
            return version

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_version_mermaid(
            str(workflow_id), 1, Repository(), _MOCK_WORKSPACE
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio()
async def test_get_workflow_version_mermaid_raises_404_for_workflow_not_found() -> None:
    """get_workflow_version_mermaid returns 404 when get_version_by_number raises WorkflowNotFoundError."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            return workflow_id

        async def get_version_by_number(self, wf_id, version_number):
            raise WorkflowNotFoundError("workflow gone")

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_version_mermaid(
            str(workflow_id), 1, Repository(), _MOCK_WORKSPACE
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_get_workflow_version_mermaid_raises_404_for_version_not_found() -> None:
    """get_workflow_version_mermaid returns 404 when get_version_by_number raises WorkflowVersionNotFoundError."""
    workflow_id = uuid4()

    class Repository:
        async def resolve_workflow_ref(
            self, workflow_ref, *, include_archived=True, workspace_id=None
        ):
            return workflow_id

        async def get_version_by_number(self, wf_id, version_number):
            raise WorkflowVersionNotFoundError("v99")

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_version_mermaid(
            str(workflow_id), 1, Repository(), _MOCK_WORKSPACE
        )

    assert exc_info.value.status_code == 404
