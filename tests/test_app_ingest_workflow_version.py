"""Tests for the workflow version ingestion endpoints."""

import asyncio
import textwrap
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT
from orcheo.models.workflow import WorkflowDraftAccess
from orcheo_backend.app import create_app, ingest_workflow_version
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowNotFoundError,
)
from orcheo_backend.app.schemas.workflows import WorkflowVersionIngestRequest


def test_ingest_workflow_version_endpoint_creates_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LangGraph scripts can be submitted to create workflow versions."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
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
    assert "summary" not in version["graph"]
    assert "index" in version["graph"]
    assert isinstance(version["graph"]["index"].get("cron"), list)


def test_ingest_workflow_version_invalid_script_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid LangGraph scripts return a 400 error."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
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


def test_ingest_workflow_version_missing_workflow_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ingesting a script for a non-existent workflow returns 404."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    repository = InMemoryWorkflowRepository()
    app = create_app(repository)
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
async def test_ingest_workflow_version_raises_not_found_error() -> None:
    """Repository lookups raising ``WorkflowNotFoundError`` propagate as 404s."""

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
            tenant_id: str | None = None,
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

    _mock_tenant = SimpleNamespace(tenant_id=uuid4())
    with pytest.raises(HTTPException) as exc_info:
        await ingest_workflow_version(
            str(workflow_id), request, repository, _mock_tenant
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Workflow not found"
