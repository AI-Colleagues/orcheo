"""Credential and workflow coverage tests for orcheo_backend.app."""

from __future__ import annotations
import importlib
from typing import Any
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient


backend_app = importlib.import_module("orcheo_backend.app")


def _create_workflow(api_client: TestClient) -> str:
    response = api_client.post(
        "/api/workflows",
        json={"name": "Scoped Flow", "actor": "tester"},
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.asyncio
async def test_credential_health_get_without_service(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Credential health endpoint returns 503 when service missing."""
    workflow_response = api_client.post(
        "/api/workflows",
        json={"name": "Test Flow", "actor": "tester"},
    )
    workflow_id = workflow_response.json()["id"]

    monkeypatch.setitem(backend_app._credential_service_ref, "service", None)
    api_client.app.dependency_overrides[backend_app.get_credential_service] = lambda: (
        None
    )

    response = api_client.get(f"/api/workflows/{workflow_id}/credentials/health")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


@pytest.mark.asyncio
async def test_credential_health_validate_without_service(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Credential validation endpoint returns 503 when service missing."""
    workflow_response = api_client.post(
        "/api/workflows",
        json={"name": "Test Flow", "actor": "tester"},
    )
    workflow_id = workflow_response.json()["id"]

    monkeypatch.setitem(backend_app._credential_service_ref, "service", None)
    api_client.app.dependency_overrides[backend_app.get_credential_service] = lambda: (
        None
    )

    response = api_client.post(
        f"/api/workflows/{workflow_id}/credentials/validate",
        json={"actor": "tester"},
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_delete_credential_not_found(api_client: TestClient) -> None:
    """Deleting a non-existent credential returns 404."""
    missing_id = uuid4()
    response = api_client.delete(f"/api/credentials/{missing_id}")
    assert response.status_code == 404


def test_delete_credential_scope_violation(api_client: TestClient) -> None:
    """Deleting credential with mismatched workflow raises 403."""
    workflow_id = _create_workflow(api_client)
    other_workflow_id = _create_workflow(api_client)

    create_response = api_client.post(
        "/api/credentials",
        json={
            "name": "Scoped Cred",
            "provider": "test",
            "secret": "secret",
            "actor": "tester",
            "access": "scoped",
            "workflow_id": workflow_id,
        },
    )
    credential_id = create_response.json()["id"]

    response = api_client.delete(
        f"/api/credentials/{credential_id}",
        params={"workflow_id": other_workflow_id},
    )
    assert response.status_code == 403


def test_create_credential_with_value_error(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backend surfaces ValueError from credential vault as 422."""
    vault = api_client.app.state.vault

    def mock_create_credential(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Invalid credential configuration")

    monkeypatch.setattr(vault, "create_credential", mock_create_credential)

    response = api_client.post(
        "/api/credentials",
        json={
            "name": "Test Cred",
            "provider": "test",
            "secret": "secret",
            "actor": "tester",
            "access": "shared",
            "kind": "secret",
        },
    )
    assert response.status_code == 422


def test_list_workflows_includes_archived(api_client: TestClient) -> None:
    """Workflows endpoint optionally returns archived entries."""
    create_response = api_client.post(
        "/api/workflows",
        json={"name": "To Archive", "actor": "tester"},
    )
    workflow_id = create_response.json()["id"]

    api_client.delete(f"/api/workflows/{workflow_id}", params={"actor": "tester"})

    response = api_client.get("/api/workflows")
    assert workflow_id not in [w["id"] for w in response.json()]

    response = api_client.get("/api/workflows?include_archived=true")
    assert any(w["id"] == workflow_id for w in response.json())


def test_ingest_workflow_version_script_error(api_client: TestClient) -> None:
    """Ingest endpoint returns 400 for bad script payloads."""
    workflow_response = api_client.post(
        "/api/workflows",
        json={"name": "Bad Script", "actor": "tester"},
    )
    workflow_id = workflow_response.json()["id"]

    response = api_client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={
            "script": "invalid python code!!!",
            "entrypoint": "app",
            "created_by": "tester",
        },
    )
    assert response.status_code == 400


def test_workflow_credential_readiness_endpoint(api_client: TestClient) -> None:
    """Readiness reports available and missing workflow credentials."""
    workflow_id = _create_workflow(api_client)

    response = api_client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={
            "script": """
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.telegram import MessageTelegram

class TelegramInput(BaseModel):
    message: str = Field(description="Message to send")

def build_tool_graph() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node(
        "send_telegram",
        MessageTelegram(
            name="send_telegram",
            chat_id="{{config.configurable.telegram_chat_id}}",
            message="{{inputs.message}}",
        ),
    )
    graph.add_edge(START, "send_telegram")
    graph.add_edge("send_telegram", END)
    return graph

def orcheo_workflow() -> StateGraph:
    graph = StateGraph(State)
    agent = AgentNode(
        name="agent",
        ai_model="openai:gpt-4o-mini",
        model_kwargs={"api_key": "[[openai_api_key]]"},
        workflow_tools=[
            {
                "name": "send_telegram_message",
                "description": "Send a Telegram message.",
                "graph": build_tool_graph(),
                "args_schema": TelegramInput,
            }
        ],
    )
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph
""",
            "created_by": "tester",
            "runnable_config": {
                "configurable": {"telegram_chat_id": "[[telegram_chat_id]]"}
            },
        },
    )
    assert response.status_code == 201

    for name, provider in [
        ("openai_api_key", "openai"),
        ("telegram_token", "telegram"),
    ]:
        create_response = api_client.post(
            "/api/credentials",
            json={
                "name": name,
                "provider": provider,
                "secret": "secret",
                "actor": "tester",
                "access": "scoped",
                "workflow_id": workflow_id,
            },
        )
        assert create_response.status_code == 201

    readiness = api_client.get(f"/api/workflows/{workflow_id}/credentials/readiness")
    assert readiness.status_code == 200

    payload = readiness.json()
    assert payload["status"] == "missing"
    assert payload["available_credentials"] == [
        "openai_api_key",
        "telegram_token",
    ]
    assert payload["missing_credentials"] == ["telegram_chat_id"]


def test_workflow_credential_readiness_ignores_optional_gemini_auth_files(
    api_client: TestClient,
) -> None:
    """Gemini optional auth placeholders should not be treated as required."""
    workflow_id = _create_workflow(api_client)

    response = api_client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={
            "script": """
from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.gemini import GeminiNode

def orcheo_workflow() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node(
        "gemini",
        GeminiNode(
            name="gemini",
            prompt="Implement the task",
            working_directory="/workspace/agents",
        ),
    )
    graph.add_edge(START, "gemini")
    graph.add_edge("gemini", END)
    return graph
""",
            "created_by": "tester",
        },
    )
    assert response.status_code == 201

    readiness = api_client.get(f"/api/workflows/{workflow_id}/credentials/readiness")
    assert readiness.status_code == 200

    payload = readiness.json()
    assert payload["status"] == "not_required"
    assert payload["referenced_credentials"] == []
    assert payload["missing_credentials"] == []
