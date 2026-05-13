"""Integration-style tests for triggering workflow runs via the HTTP executor."""

from __future__ import annotations
import json
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    Role,
    Workspace,
    WorkspaceMembership,
)
from orcheo.workspace.models import WorkspaceContext
from orcheo_backend.app import create_app
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.workspace.dependencies import (
    reset_workspace_state,
    resolve_workspace_context,
    set_workspace_repository,
)
from orcheo_sdk import HttpWorkflowExecutor, OrcheoClient


def test_http_executor_triggers_run_against_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()
    reset_workspace_state()

    workspace_id = uuid4()
    workspace_repo = InMemoryWorkspaceRepository()
    workspace_repo.create_workspace(
        Workspace(id=workspace_id, slug="default", name="Default Workspace")
    )
    workspace_repo.add_membership(
        WorkspaceMembership(workspace_id=workspace_id, user_id="sdk", role=Role.OWNER)
    )
    set_workspace_repository(workspace_repo)

    repository = InMemoryWorkflowRepository()
    app = create_app(repository)
    workspace_context = WorkspaceContext(
        workspace_id=workspace_id,
        workspace_slug="default",
        user_id="sdk",
        role=Role.OWNER,
    )
    app.dependency_overrides[resolve_workspace_context] = lambda: workspace_context

    with TestClient(app) as api_client:
        transport = httpx.MockTransport(
            lambda request: _dispatch_to_app(api_client, request)
        )
        http_client = httpx.Client(transport=transport, base_url="http://testserver")
        sdk_client = OrcheoClient(base_url="http://testserver")
        executor = HttpWorkflowExecutor(
            client=sdk_client,
            http_client=http_client,
            auth_token="token-123",
            max_retries=0,
        )

        try:
            workflow_id, version_id = _create_workflow_and_version(http_client)
            payload = executor.trigger_run(
                workflow_id,
                workflow_version_id=version_id,
                triggered_by="runner",
                inputs={"value": 1},
            )
        finally:
            http_client.close()

    assert payload["status"] == "pending"
    assert payload["triggered_by"] == "runner"
    assert payload["input_payload"] == {"value": 1}


def _create_workflow_and_version(http_client: httpx.Client) -> tuple[str, str]:
    create_workflow = http_client.post(
        "/api/workflows",
        json={"name": "SDK Flow", "actor": "sdk"},
    )
    create_workflow.raise_for_status()
    workflow_id = create_workflow.json()["id"]

    script = """
from langgraph.graph import END, START, StateGraph

def build_graph():
    graph = StateGraph(dict)

    def start(state):
        return state

    graph.add_node("start", start)
    graph.add_edge(START, "start")
    graph.add_edge("start", END)
    return graph
""".strip()

    create_version = http_client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={
            "script": script,
            "entrypoint": "build_graph",
            "metadata": {},
            "created_by": "sdk",
        },
    )
    create_version.raise_for_status()
    version_id = create_version.json()["id"]

    return workflow_id, version_id


def _dispatch_to_app(api_client: TestClient, request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"

    response = api_client.request(
        request.method,
        path,
        headers={
            key: value
            for key, value in request.headers.items()
            if key.lower() != "host"
        },
        content=request.content,
    )

    return httpx.Response(
        status_code=response.status_code,
        headers=response.headers,
        content=response.content,
    )


def test_http_executor_includes_runnable_config() -> None:
    sdk_client = OrcheoClient(base_url="http://testserver")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["runnable_config"] == {"mode": "fast"}
        return httpx.Response(201, json={"status": "pending"})

    transport = httpx.MockTransport(handler)
    executor = HttpWorkflowExecutor(
        client=sdk_client,
        auth_token="token-123",
        transport=transport,
        max_retries=0,
    )

    payload = executor.trigger_run(
        "wf-1",
        workflow_version_id="ver-1",
        triggered_by="runner",
        runnable_config={"mode": "fast"},
    )

    assert payload["status"] == "pending"
