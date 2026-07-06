"""Tests for the workflow version ingestion endpoints."""

import asyncio
import os
import textwrap
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT
from orcheo.models import WorkflowDraftAccess
from orcheo.workspace.models import Role, WorkspaceContext
from orcheo_backend.app import create_app, ingest_workflow_version
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowNotFoundError,
)
from orcheo_backend.app.schemas.workflows import WorkflowVersionIngestRequest
from orcheo_backend.app.workspace.dependencies import resolve_workspace_context


def test_ingest_workflow_version_endpoint_creates_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LangGraph scripts can be submitted to create workflow versions."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "allow_client_uploads")
    reset_authentication_state()

    repository = InMemoryWorkflowRepository()
    workflow = asyncio.run(
        repository.create_workflow(
            name="LangGraph",
            slug=None,
            description=None,
            tags=[],
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
    )

    app = create_app(repository)
    workspace_context = WorkspaceContext(
        workspace_id=uuid4(),
        workspace_slug="default",
        user_id="tester",
        role=Role.OWNER,
    )
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context
    client = TestClient(app)

    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        def build_graph():
            graph = StateGraph(State)
            graph.add_node("noop", lambda state: state)
            graph.set_entry_point("noop")
            graph.set_finish_point("noop")
            return graph
        """
    )

    response = client.post(
        f"/api/workflows/{workflow.id}/versions/ingest",
        json={
            "script": script,
            "entrypoint": "build_graph",
            "metadata": {"language": "python"},
            "notes": "Initial LangGraph import",
            "created_by": "tester",
        },
    )

    assert response.status_code == 201
    version = response.json()
    assert version["metadata"] == {"language": "python"}
    assert version["notes"] == "Initial LangGraph import"
    assert version["graph"]["format"] == LANGGRAPH_SCRIPT_FORMAT
    assert "index" in version["graph"]
    assert isinstance(version["graph"]["index"].get("cron"), list)
    assert isinstance(version["graph"]["index"].get("mermaid"), str)


def test_ingest_workflow_version_invalid_script_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid LangGraph scripts return a 400 error."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "allow_client_uploads")
    reset_authentication_state()

    repository = InMemoryWorkflowRepository()
    workflow = asyncio.run(
        repository.create_workflow(
            name="Bad Script",
            slug=None,
            description=None,
            tags=[],
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
    )

    app = create_app(repository)
    workspace_context = WorkspaceContext(
        workspace_id=uuid4(),
        workspace_slug="default",
        user_id="tester",
        role=Role.OWNER,
    )
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context
    client = TestClient(app)

    invalid_script = "this is not valid python code!!!"

    response = client.post(
        f"/api/workflows/{workflow.id}/versions/ingest",
        json={
            "script": invalid_script,
            "entrypoint": "build_graph",
            "created_by": "tester",
        },
    )

    assert response.status_code == 400
    assert "detail" in response.json()


def test_ingest_workflow_version_restricted_mode_rejects_disallowed_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restricted mode rejects a script that escapes the allowlist grammar.

    Regression: the ingest route honours ``ORCHEO_WORKFLOW_DEFINITION_MODE``.
    A workflow importing ``langgraph.graph`` directly (non-Orcheo import) must
    be blocked at upload with a line-referenced 400 instead of being stored.
    """

    from orcheo.config import loader as config_loader

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "allow_client_uploads")
    monkeypatch.setenv("ORCHEO_WORKFLOW_DEFINITION_MODE", "restricted")
    config_loader.get_settings(refresh=True)
    reset_authentication_state()

    repository = InMemoryWorkflowRepository()
    workflow = asyncio.run(
        repository.create_workflow(
            name="Disallowed",
            slug=None,
            description=None,
            tags=[],
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
    )

    app = create_app(repository)
    workspace_context = WorkspaceContext(
        workspace_id=uuid4(),
        workspace_slug="default",
        user_id="tester",
        role=Role.OWNER,
    )
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context
    client = TestClient(app)

    disallowed_script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph, START, END
        from orcheo.graph.state import State

        async def orcheo_workflow() -> StateGraph:
            graph = StateGraph(State)
            graph.add_edge(START, END)
            return graph
        """
    )

    response = client.post(
        f"/api/workflows/{workflow.id}/versions/ingest",
        json={
            "script": disallowed_script,
            "entrypoint": "orcheo_workflow",
            "created_by": "tester",
        },
    )

    assert response.status_code == 400
    assert "langgraph.graph" in str(response.json()["detail"])
    # The rejection must happen before any version is persisted.
    assert asyncio.run(repository.list_versions(workflow.id)) == []


def test_ingest_workflow_version_missing_workflow_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ingesting a script for a non-existent workflow returns 404."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "allow_client_uploads")
    reset_authentication_state()

    repository = InMemoryWorkflowRepository()
    app = create_app(repository)
    workspace_context = WorkspaceContext(
        workspace_id=uuid4(),
        workspace_slug="default",
        user_id="tester",
        role=Role.OWNER,
    )
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context
    client = TestClient(app)

    missing_id = str(uuid4())

    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        def build_graph():
            graph = StateGraph(State)
            graph.add_node("noop", lambda state: state)
            graph.set_entry_point("noop")
            graph.set_finish_point("noop")
            return graph
        """
    )

    response = client.post(
        f"/api/workflows/{missing_id}/versions/ingest",
        json={
            "script": script,
            "entrypoint": "build_graph",
            "created_by": "tester",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow not found"


@pytest.mark.asyncio
async def test_ingest_workflow_version_raises_not_found_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repository lookups raising ``WorkflowNotFoundError`` propagate as 404s."""

    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "allow_client_uploads")

    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        def build_graph():
            graph = StateGraph(State)
            graph.add_node("noop", lambda state: state)
            graph.set_entry_point("noop")
            graph.set_finish_point("noop")
            return graph
        """
    )

    request = WorkflowVersionIngestRequest(
        script=script,
        entrypoint="build_graph",
        created_by="tester",
    )

    class FailingRepository(InMemoryWorkflowRepository):
        async def resolve_workflow_ref(
            self,
            workflow_ref: str,
            *,
            include_archived: bool = True,
            workspace_id: str | None = None,
            team_id: str | None = None,
        ) -> UUID:
            del include_archived
            return UUID(str(workflow_ref))

        async def create_version(
            self,
            workflow_id: UUID,
            *,
            graph: dict[str, object],
            metadata: dict[str, object],
            runnable_config: dict[str, object] | None = None,
            notes: str | None,
            created_by: str,
        ):
            raise WorkflowNotFoundError(str(workflow_id))

    repository = FailingRepository()
    workflow_id = uuid4()

    _mock_workspace = SimpleNamespace(workspace_id=uuid4())
    with pytest.raises(HTTPException) as exc_info:
        await ingest_workflow_version(
            str(workflow_id), request, repository, _mock_workspace
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Workflow not found"


def test_ingest_workflow_version_stores_mermaid_in_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When mermaid rendering succeeds, the diagram is written into the graph index."""
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "allow_client_uploads")
    reset_authentication_state()

    import importlib

    _workflows_module = importlib.import_module("orcheo_backend.app.routers.workflows")
    monkeypatch.setattr(
        _workflows_module,
        "render_mermaid_from_graph_payload_full_env",
        lambda payload: "graph TD\n  A --> B",
    )

    repository = InMemoryWorkflowRepository()
    workflow = asyncio.run(
        repository.create_workflow(
            name="MermaidWorkflow",
            slug=None,
            description=None,
            tags=[],
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
    )

    app = create_app(repository)
    workspace_context = WorkspaceContext(
        workspace_id=uuid4(),
        workspace_slug="default",
        user_id="tester",
        role=Role.OWNER,
    )
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context
    client = TestClient(app)

    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        def build_graph():
            graph = StateGraph(State)
            graph.add_node("noop", lambda state: state)
            graph.set_entry_point("noop")
            graph.set_finish_point("noop")
            return graph
        """
    )

    response = client.post(
        f"/api/workflows/{workflow.id}/versions/ingest",
        json={
            "script": script,
            "entrypoint": "build_graph",
            "created_by": "tester",
        },
    )

    assert response.status_code == 201
    index = response.json()["graph"]["index"]
    assert index.get("mermaid") == "graph TD\n  A --> B"


def test_ingest_workflow_version_rejects_missing_required_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Templates that declare unavailable required plugins are rejected.

    Covers routers/workflows.py line 935 (the HTTPException raised when
    ``missing_required_plugins`` reports at least one unavailable plugin) and
    verifies the failure is recorded via ``set_workflow_upload_error``.
    """
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "allow_client_uploads")
    reset_authentication_state()

    import importlib

    _workflows_module = importlib.import_module("orcheo_backend.app.routers.workflows")
    monkeypatch.setattr(
        _workflows_module,
        "missing_required_plugins",
        lambda required: list(required),
    )

    repository = InMemoryWorkflowRepository()
    workflow = asyncio.run(
        repository.create_workflow(
            name="PluginGated",
            slug=None,
            description=None,
            tags=[],
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
    )

    app = create_app(repository)
    workspace_context = WorkspaceContext(
        workspace_id=uuid4(),
        workspace_slug="default",
        user_id="tester",
        role=Role.OWNER,
    )
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context
    client = TestClient(app)

    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        def build_graph():
            graph = StateGraph(State)
            graph.add_node("noop", lambda state: state)
            graph.set_entry_point("noop")
            graph.set_finish_point("noop")
            return graph
        """
    )

    response = client.post(
        f"/api/workflows/{workflow.id}/versions/ingest",
        json={
            "script": script,
            "entrypoint": "build_graph",
            "created_by": "tester",
            "metadata": {"template": {"requiredPlugins": ["wecom_listener"]}},
        },
    )

    assert response.status_code == 400
    assert "wecom_listener" in response.json()["detail"]

    stored = asyncio.run(repository.get_workflow(workflow.id))
    assert stored.upload_error is not None
    assert "wecom_listener" in stored.upload_error.message


def test_ingest_workflow_version_rejects_invalid_inline_configurable_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inline configurable schema without a resolvable default is rejected.

    Covers routers/workflows.py line 973 (the ``raise`` re-propagating the
    HTTPException from ``_resolve_ingest_configurable_schema`` after recording
    the upload failure).
    """
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "allow_client_uploads")
    reset_authentication_state()

    repository = InMemoryWorkflowRepository()
    workflow = asyncio.run(
        repository.create_workflow(
            name="BadInlineSchema",
            slug=None,
            description=None,
            tags=[],
            draft_access=WorkflowDraftAccess.PERSONAL,
            actor="tester",
        )
    )

    app = create_app(repository)
    workspace_context = WorkspaceContext(
        workspace_id=uuid4(),
        workspace_slug="default",
        user_id="tester",
        role=Role.OWNER,
    )
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context
    client = TestClient(app)

    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        def build_graph():
            graph = StateGraph(State)
            graph.add_node("noop", lambda state: state)
            graph.set_entry_point("noop")
            graph.set_finish_point("noop")
            return graph
        """
    )

    response = client.post(
        f"/api/workflows/{workflow.id}/versions/ingest",
        json={
            "script": script,
            "entrypoint": "build_graph",
            "created_by": "tester",
            "runnable_config": {
                "configurable": {
                    # A schema declaration (has a discriminator key: "properties")
                    # but no "default"/"const"/non-empty "enum" to resolve at
                    # runtime, so split_configurable raises ConfigurableSchemaError.
                    "broken_field": {"type": "object", "properties": {}},
                }
            },
        },
    )

    assert response.status_code == 400
    assert "broken_field" in response.json()["detail"]

    stored = asyncio.run(repository.get_workflow(workflow.id))
    assert stored.upload_error is not None
    assert "broken_field" in stored.upload_error.message
