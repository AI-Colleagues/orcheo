"""Integration tests for workflow CRUD endpoints."""

from __future__ import annotations
from typing import Any
from uuid import uuid4
from fastapi.testclient import TestClient


_MANAGED_HANDLE = "orcheo-vibe-agent"


def _user_workflows(workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return workflows excluding the system-managed vibe agent."""
    return [w for w in workflows if w.get("handle") != _MANAGED_HANDLE]


def test_list_workflows_empty(client: TestClient) -> None:
    response = client.get("/api/workflows")
    assert response.status_code == 200
    assert _user_workflows(response.json()) == []


def test_list_workflows_excludes_archived_by_default(client: TestClient) -> None:
    active_response = client.post(
        "/api/workflows",
        json={"name": "Active Workflow", "slug": "active", "actor": "admin"},
    )
    assert active_response.status_code == 201

    archived_response = client.post(
        "/api/workflows",
        json={"name": "Archived Workflow", "slug": "archived", "actor": "admin"},
    )
    archived_id = archived_response.json()["id"]
    client.delete(f"/api/workflows/{archived_id}?actor=admin")

    list_response = client.get("/api/workflows")
    workflows = _user_workflows(list_response.json())
    assert len(workflows) == 1
    assert workflows[0]["name"] == "Active Workflow"
    assert not workflows[0]["is_archived"]


def test_list_workflows_includes_archived_with_flag(client: TestClient) -> None:
    client.post(
        "/api/workflows",
        json={"name": "Active Workflow", "slug": "active", "actor": "admin"},
    )

    archived_response = client.post(
        "/api/workflows",
        json={"name": "Archived Workflow", "slug": "archived", "actor": "admin"},
    )
    archived_id = archived_response.json()["id"]
    client.delete(f"/api/workflows/{archived_id}?actor=admin")

    list_response = client.get("/api/workflows?include_archived=true")
    workflows = _user_workflows(list_response.json())
    assert len(workflows) == 2

    workflow_names = {wf["name"] for wf in workflows}
    assert workflow_names == {"Active Workflow", "Archived Workflow"}

    for wf in workflows:
        if wf["name"] == "Active Workflow":
            assert not wf["is_archived"]
        else:
            assert wf["is_archived"]


def test_create_and_get_workflow(client: TestClient) -> None:
    create_response = client.post(
        "/api/workflows",
        json={
            "name": "Test Workflow",
            "slug": "test-workflow",
            "description": "A test workflow",
            "tags": ["test"],
            "actor": "admin",
        },
    )
    workflow_id = create_response.json()["id"]

    get_response = client.get(f"/api/workflows/{workflow_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Test Workflow"


def test_update_workflow(client: TestClient) -> None:
    create_response = client.post(
        "/api/workflows",
        json={"name": "Original Name", "slug": "original-slug", "actor": "admin"},
    )
    workflow_id = create_response.json()["id"]

    update_response = client.put(
        f"/api/workflows/{workflow_id}",
        json={
            "name": "Updated Name",
            "description": "Updated description",
            "tags": ["updated"],
            "is_archived": False,
            "actor": "admin",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Updated Name"


def test_archive_workflow(client: TestClient) -> None:
    create_response = client.post(
        "/api/workflows",
        json={"name": "To Archive", "slug": "to-archive", "actor": "admin"},
    )
    workflow_id = create_response.json()["id"]

    delete_response = client.delete(f"/api/workflows/{workflow_id}?actor=admin")
    assert delete_response.status_code == 200
    assert delete_response.json()["is_archived"] is True


def test_get_workflow_not_found(client: TestClient) -> None:
    random_id = str(uuid4())
    response = client.get(f"/api/workflows/{random_id}")
    assert response.status_code == 404
