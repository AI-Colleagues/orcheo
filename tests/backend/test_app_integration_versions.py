"""Integration tests for workflow version endpoints."""

from __future__ import annotations
from typing import Any
import pytest
from fastapi.testclient import TestClient


def _langgraph_script(node_name: str = "step_one", response: str = "ok") -> str:
    return f"""
from langgraph.graph import END, START, StateGraph

def build_graph():
    graph = StateGraph(dict)

    def {node_name}(state):
        return {{"message": "{response}"}}

    graph.add_node("{node_name}", {node_name})
    graph.add_edge(START, "{node_name}")
    graph.add_edge("{node_name}", END)
    return graph
""".strip()


def _ingest_version(client: TestClient, workflow_id: str, *, script: str) -> dict:
    response = client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={"script": script, "entrypoint": "build_graph", "created_by": "admin"},
    )
    assert response.status_code == 201
    return response.json()


def test_create_workflow_version_route_removed(client: TestClient) -> None:
    workflow_response = client.post(
        "/api/workflows",
        json={"name": "Test Workflow", "slug": "test-workflow", "actor": "admin"},
    )
    workflow_id = workflow_response.json()["id"]

    response = client.post(
        f"/api/workflows/{workflow_id}/versions",
        json={
            "graph": {"nodes": [], "edges": []},
            "metadata": {"test": "data"},
            "notes": "Initial version",
            "created_by": "admin",
        },
    )
    assert response.status_code == 405


def test_list_workflow_versions(client: TestClient) -> None:
    workflow_response = client.post(
        "/api/workflows",
        json={"name": "Test Workflow", "slug": "test-workflow", "actor": "admin"},
    )
    workflow_id = workflow_response.json()["id"]

    _ingest_version(client, workflow_id, script=_langgraph_script())

    versions_response = client.get(f"/api/workflows/{workflow_id}/versions")
    assert versions_response.status_code == 200
    assert len(versions_response.json()) == 1
    assert isinstance(versions_response.json()[0]["mermaid"], str)


def test_get_workflow_version(client: TestClient) -> None:
    workflow_response = client.post(
        "/api/workflows",
        json={"name": "Test Workflow", "slug": "test-workflow", "actor": "admin"},
    )
    workflow_id = workflow_response.json()["id"]

    _ingest_version(client, workflow_id, script=_langgraph_script())

    version_response = client.get(f"/api/workflows/{workflow_id}/versions/1")
    assert version_response.status_code == 200
    assert version_response.json()["version"] == 1
    assert isinstance(version_response.json()["mermaid"], str)


def test_diff_workflow_versions(client: TestClient) -> None:
    workflow_response = client.post(
        "/api/workflows",
        json={"name": "Test Workflow", "slug": "test-workflow", "actor": "admin"},
    )
    workflow_id = workflow_response.json()["id"]

    _ingest_version(
        client,
        workflow_id,
        script=_langgraph_script(node_name="first_step", response="first"),
    )
    _ingest_version(
        client,
        workflow_id,
        script=_langgraph_script(node_name="second_step", response="second"),
    )

    diff_response = client.get(f"/api/workflows/{workflow_id}/versions/1/diff/2")
    assert diff_response.status_code == 200
    assert diff_response.json()["base_version"] == 1
    assert diff_response.json()["target_version"] == 2
    assert any("second_step" in line for line in diff_response.json()["diff"])


def test_ingest_workflow_version_invalid_script(client: TestClient) -> None:
    workflow_response = client.post(
        "/api/workflows",
        json={"name": "Test Workflow", "slug": "test-workflow", "actor": "admin"},
    )
    workflow_id = workflow_response.json()["id"]

    response = client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={"script": "def broken(:\n    pass", "created_by": "admin"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]

    workflow_response = client.get(f"/api/workflows/{workflow_id}")
    assert workflow_response.status_code == 200
    workflow_payload = workflow_response.json()
    assert workflow_payload["upload_error"]["message"] == detail
    assert workflow_payload["upload_error"]["occurred_at"]

    _ingest_version(client, workflow_id, script=_langgraph_script())

    recovered_response = client.get(f"/api/workflows/{workflow_id}")
    assert recovered_response.status_code == 200
    assert recovered_response.json()["upload_error"] is None


def test_ingest_upload_error_clear_failure_does_not_fail_ingest(
    client: TestClient,
    repository: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing best-effort upload_error clear must not 500 a successful ingest."""
    workflow_response = client.post(
        "/api/workflows",
        json={"name": "Test Workflow", "slug": "test-workflow", "actor": "admin"},
    )
    workflow_id = workflow_response.json()["id"]

    # Set an upload_error via an invalid ingest so the success path tries to clear it.
    failed = client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={"script": "def broken(:\n    pass", "created_by": "admin"},
    )
    assert failed.status_code == 400

    async def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("clear failed")

    monkeypatch.setattr(repository, "set_workflow_upload_error", _raise)

    recovered = client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={
            "script": _langgraph_script(),
            "entrypoint": "build_graph",
            "created_by": "admin",
        },
    )
    assert recovered.status_code == 201


def test_update_workflow_version_runnable_config(client: TestClient) -> None:
    workflow_response = client.post(
        "/api/workflows",
        json={"name": "Test Workflow", "slug": "test-workflow", "actor": "admin"},
    )
    workflow_id = workflow_response.json()["id"]

    _ingest_version(client, workflow_id, script=_langgraph_script())

    update_response = client.put(
        f"/api/workflows/{workflow_id}/versions/1/runnable-config",
        json={
            "runnable_config": {"tags": ["studio"], "run_name": "cfg"},
            "actor": "ui",
        },
    )
    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["version"] == 1
    assert payload["runnable_config"] == {"tags": ["studio"], "run_name": "cfg"}
