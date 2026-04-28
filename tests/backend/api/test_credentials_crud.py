from __future__ import annotations
from uuid import UUID, uuid4
from fastapi import status
from fastapi.testclient import TestClient
from orcheo.models import CredentialHealthStatus
from orcheo.vault import InMemoryCredentialVault, WorkflowScopeError


def _create_workflow(api_client: TestClient) -> str:
    response = api_client.post(
        "/api/workflows",
        json={"name": "Credential Flow", "actor": "tester"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_credential_template_crud_and_issuance(api_client: TestClient) -> None:
    create_response = api_client.post(
        "/api/credentials/templates",
        json={
            "name": "GitHub",
            "provider": "github",
            "scopes": ["repo:read"],
            "description": "GitHub token",
            "kind": "secret",
            "actor": "tester",
        },
    )
    assert create_response.status_code == 201
    template = create_response.json()
    template_id = template["id"]

    fetch_response = api_client.get(f"/api/credentials/templates/{template_id}")
    assert fetch_response.status_code == 200

    list_response = api_client.get("/api/credentials/templates")
    assert list_response.status_code == 200
    assert any(item["id"] == template_id for item in list_response.json())

    update_response = api_client.patch(
        f"/api/credentials/templates/{template_id}",
        json={"description": "Rotated", "actor": "tester"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Rotated"

    issue_response = api_client.post(
        f"/api/credentials/templates/{template_id}/issue",
        json={
            "template_id": template_id,
            "secret": "sup3r-secret",
            "actor": "tester",
            "name": "GitHub Prod",
        },
    )
    assert issue_response.status_code == 201
    issued = issue_response.json()
    assert issued["name"] == "GitHub Prod"
    assert issued["template_id"] == template_id

    vault: InMemoryCredentialVault = api_client.app.state.vault
    stored = vault.list_credentials()
    assert any(item.template_id == UUID(template_id) for item in stored)

    delete_response = api_client.delete(f"/api/credentials/templates/{template_id}")
    assert delete_response.status_code == 204

    get_response = api_client.get(f"/api/credentials/templates/{template_id}")
    assert get_response.status_code == 404


def test_list_credentials_endpoint_returns_vault_entries(
    api_client: TestClient,
) -> None:
    create_response = api_client.post(
        "/api/credentials/templates",
        json={
            "name": "Stripe Secret",
            "provider": "stripe",
            "scopes": ["payments:read"],
            "kind": "secret",
            "actor": "tester",
        },
    )
    assert create_response.status_code == 201
    template_id = create_response.json()["id"]

    issue_response = api_client.post(
        f"/api/credentials/templates/{template_id}/issue",
        json={
            "template_id": template_id,
            "secret": "sk_test_orcheo",
            "actor": "tester",
            "name": "Stripe Production",
        },
    )
    assert issue_response.status_code == 201
    issued = issue_response.json()

    list_response = api_client.get("/api/credentials")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert isinstance(payload, list)
    assert payload

    credential = next(item for item in payload if item["id"] == issued["credential_id"])
    assert credential["name"] == issued["name"]
    assert credential["provider"] == issued["provider"]
    assert credential["status"] == CredentialHealthStatus.UNKNOWN.value
    assert credential["access"] in {"scoped", "shared"}
    assert credential["owner"] == "tester"
    assert credential["secret_preview"]


def test_create_credential(api_client: TestClient) -> None:
    workflow_id = _create_workflow(api_client)
    response = api_client.post(
        "/api/credentials",
        json={
            "name": "Canvas API",
            "provider": "api",
            "secret": "sk_test_canvas",
            "actor": "tester",
            "access": "scoped",
            "workflow_id": workflow_id,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "Canvas API"
    assert payload["provider"] == "api"
    assert payload["owner"] == "tester"
    assert payload["access"] == "scoped"

    fetch_response = api_client.get(
        "/api/credentials",
        params={"workflow_id": workflow_id},
    )

    assert fetch_response.status_code == 200
    entries = fetch_response.json()
    assert any(entry["id"] == payload["id"] for entry in entries)


def test_list_credentials_without_workflow_includes_scoped_entries(
    api_client: TestClient,
) -> None:
    workflow_id = _create_workflow(api_client)
    create_response = api_client.post(
        "/api/credentials",
        json={
            "name": "Scoped API",
            "provider": "api",
            "secret": "sk_test_scoped",
            "actor": "tester",
            "access": "scoped",
            "workflow_id": workflow_id,
        },
    )
    assert create_response.status_code == 201
    credential_id = create_response.json()["id"]

    list_response = api_client.get("/api/credentials")

    assert list_response.status_code == 200
    entries = list_response.json()
    credential = next(item for item in entries if item["id"] == credential_id)
    assert credential["access"] == "scoped"
    assert credential["workflow_id"] == workflow_id


def test_reveal_credential_secret(api_client: TestClient) -> None:
    workflow_id = _create_workflow(api_client)
    create_response = api_client.post(
        "/api/credentials",
        json={
            "name": "Canvas API",
            "provider": "api",
            "secret": "sk_test_canvas",
            "actor": "tester",
            "access": "scoped",
            "workflow_id": workflow_id,
        },
    )
    assert create_response.status_code == 201
    credential_id = create_response.json()["id"]

    reveal_response = api_client.get(
        f"/api/credentials/{credential_id}/secret",
        params={"workflow_id": workflow_id},
    )
    assert reveal_response.status_code == 200
    payload = reveal_response.json()
    assert payload["id"] == credential_id
    assert payload["secret"] == "sk_test_canvas"


def test_update_credential(api_client: TestClient) -> None:
    workflow_id = _create_workflow(api_client)
    create_response = api_client.post(
        "/api/credentials",
        json={
            "name": "Canvas API",
            "provider": "api",
            "secret": "sk_test_canvas",
            "actor": "tester",
            "access": "scoped",
            "workflow_id": workflow_id,
        },
    )
    assert create_response.status_code == 201
    credential_id = create_response.json()["id"]

    update_response = api_client.patch(
        f"/api/credentials/{credential_id}",
        json={
            "name": "Canvas API Prod",
            "provider": "openai",
            "secret": "sk_test_updated",
            "actor": "tester",
            "access": "scoped",
            "workflow_id": workflow_id,
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["id"] == credential_id
    assert updated["name"] == "Canvas API Prod"
    assert updated["provider"] == "openai"

    reveal_response = api_client.get(
        f"/api/credentials/{credential_id}/secret",
        params={"workflow_id": workflow_id},
    )
    assert reveal_response.status_code == 200
    assert reveal_response.json()["secret"] == "sk_test_updated"


def test_update_credential_rejects_scoped_access_without_workflow(
    api_client: TestClient,
) -> None:
    create_response = api_client.post(
        "/api/credentials",
        json={
            "name": "Canvas API",
            "provider": "api",
            "secret": "sk_test_canvas",
            "actor": "tester",
            "access": "shared",
        },
    )
    assert create_response.status_code == 201
    credential_id = create_response.json()["id"]

    update_response = api_client.patch(
        f"/api/credentials/{credential_id}",
        json={
            "actor": "tester",
            "access": "scoped",
        },
    )

    assert update_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "workflow_id is required" in update_response.json()["detail"]


def test_update_credential_not_found_returns_404(
    api_client: TestClient,
) -> None:
    missing_id = uuid4()
    response = api_client.patch(
        f"/api/credentials/{missing_id}",
        json={"actor": "tester", "name": "missing"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Credential not found"


def test_update_credential_scope_error_returns_403(
    api_client: TestClient,
    monkeypatch,
) -> None:
    vault = api_client.app.state.vault

    def _raise_scope_error(**kwargs):
        raise WorkflowScopeError("Access denied")

    monkeypatch.setattr(vault, "update_credential", _raise_scope_error)

    response = api_client.patch(
        f"/api/credentials/{uuid4()}",
        json={"actor": "tester", "name": "blocked"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "Access denied"


def test_create_credential_duplicate_name_returns_409(
    api_client: TestClient,
) -> None:
    workflow_id = _create_workflow(api_client)
    payload = {
        "name": "Canvas API",
        "provider": "api",
        "secret": "sk_test_canvas",
        "actor": "tester",
        "access": "scoped",
        "workflow_id": workflow_id,
    }
    first = api_client.post("/api/credentials", json=payload)
    assert first.status_code == 201

    duplicate = api_client.post("/api/credentials", json=payload)
    assert duplicate.status_code == status.HTTP_409_CONFLICT
    assert "already in use" in duplicate.json()["detail"]


def test_delete_credential(api_client: TestClient) -> None:
    workflow_id = _create_workflow(api_client)
    create_response = api_client.post(
        "/api/credentials",
        json={
            "name": "Canvas API",
            "provider": "api",
            "secret": "sk_test_canvas",
            "actor": "tester",
            "access": "scoped",
            "workflow_id": workflow_id,
        },
    )
    assert create_response.status_code == 201
    credential_id = create_response.json()["id"]

    delete_response = api_client.delete(
        f"/api/credentials/{credential_id}",
        params={"workflow_id": workflow_id},
    )
    assert delete_response.status_code == 204

    fetch_response = api_client.get(
        "/api/credentials",
        params={"workflow_id": workflow_id},
    )
    assert fetch_response.status_code == 200
    payload = fetch_response.json()
    assert all(entry["id"] != credential_id for entry in payload)
