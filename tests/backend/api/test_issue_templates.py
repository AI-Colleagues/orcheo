from __future__ import annotations
from typing import Any
from uuid import uuid4
from fastapi.testclient import TestClient
from orcheo.vault import CredentialTemplateNotFoundError, WorkflowScopeError
from .shared import backend_app


def _create_workflow(api_client: TestClient) -> str:
    response = api_client.post(
        "/api/workflows",
        json={"name": "Issue Flow", "actor": "tester"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_issue_template_without_service_returns_503(api_client: TestClient) -> None:
    create_response = api_client.post(
        "/api/credentials/templates",
        json={
            "name": "GitHub",
            "provider": "github",
            "scopes": ["repo"],
            "actor": "tester",
        },
    )
    template_id = create_response.json()["id"]

    api_client.app.dependency_overrides[backend_app.get_vault] = lambda: (
        api_client.app.state.vault
    )
    api_client.app.dependency_overrides[backend_app.get_credential_service] = lambda: (
        None
    )

    response = api_client.post(
        f"/api/credentials/templates/{template_id}/issue",
        json={"template_id": template_id, "secret": "s", "actor": "tester"},
    )

    api_client.app.dependency_overrides.clear()
    assert response.status_code == 503


def test_issue_template_value_error_returns_400(api_client: TestClient) -> None:
    create_response = api_client.post(
        "/api/credentials/templates",
        json={
            "name": "GitHub",
            "provider": "github",
            "scopes": ["repo"],
            "actor": "tester",
        },
    )
    template_id = create_response.json()["id"]

    class RaisingService:
        def issue_from_template(self, **_: Any) -> None:
            raise ValueError("invalid")

    api_client.app.dependency_overrides[backend_app.get_vault] = lambda: (
        api_client.app.state.vault
    )
    api_client.app.dependency_overrides[backend_app.get_credential_service] = lambda: (
        RaisingService()
    )

    response = api_client.post(
        f"/api/credentials/templates/{template_id}/issue",
        json={"template_id": template_id, "secret": "s", "actor": "tester"},
    )

    api_client.app.dependency_overrides.clear()
    assert response.status_code == 400


def test_issue_template_not_found_returns_404(api_client: TestClient) -> None:
    class MissingTemplateService:
        def issue_from_template(self, **_: Any) -> None:
            raise CredentialTemplateNotFoundError("missing")

    api_client.app.dependency_overrides[backend_app.get_vault] = lambda: (
        api_client.app.state.vault
    )
    api_client.app.dependency_overrides[backend_app.get_credential_service] = lambda: (
        MissingTemplateService()
    )

    response = api_client.post(
        f"/api/credentials/templates/{uuid4()}/issue",
        json={"template_id": str(uuid4()), "secret": "s", "actor": "tester"},
    )

    api_client.app.dependency_overrides.clear()
    assert response.status_code == 404


def test_issue_template_scope_violation_returns_403(api_client: TestClient) -> None:
    workflow_id = _create_workflow(api_client)
    other_workflow_id = _create_workflow(api_client)
    create_response = api_client.post(
        "/api/credentials/templates",
        json={
            "name": "Restricted",
            "provider": "internal",
            "scopes": ["read"],
            "scope": {"workflow_ids": [workflow_id]},
            "actor": "tester",
        },
    )
    template_id = create_response.json()["id"]

    class ScopeDeniedService:
        def issue_from_template(self, **_: Any) -> None:
            raise WorkflowScopeError("denied")

    api_client.app.dependency_overrides[backend_app.get_vault] = lambda: (
        api_client.app.state.vault
    )
    api_client.app.dependency_overrides[backend_app.get_credential_service] = lambda: (
        ScopeDeniedService()
    )

    response = api_client.post(
        f"/api/credentials/templates/{template_id}/issue",
        json={
            "template_id": template_id,
            "secret": "s",
            "actor": "tester",
            "workflow_id": other_workflow_id,
        },
    )

    api_client.app.dependency_overrides.clear()
    assert response.status_code == 403
