"""HTTP regression tests for the workflow ChatKit session endpoint."""

from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.chatkit_tokens import reset_chatkit_token_state


def _create_workflow(client: TestClient) -> str:
    response = client.post(
        "/api/workflows",
        json={"name": "Studio Workflow", "actor": "tester"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_chatkit_session_allows_anonymous_when_auth_is_disabled(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anonymous requests should succeed when auth enforcement is disabled."""
    monkeypatch.setenv("ORCHEO_CHATKIT_TOKEN_SIGNING_KEY", "workflow-session-key")
    reset_chatkit_token_state()

    workflow_id = _create_workflow(api_client)

    response = api_client.post(f"/api/workflows/{workflow_id}/chatkit/session")

    assert response.status_code == 200
    payload = response.json()
    assert payload["client_secret"]
    assert payload["expires_at"]


def test_chatkit_session_requires_authentication_when_enforced(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anonymous requests should fail when auth enforcement is enabled."""
    monkeypatch.setenv("ORCHEO_CHATKIT_TOKEN_SIGNING_KEY", "workflow-session-key")
    workflow_id = _create_workflow(api_client)

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()
    reset_chatkit_token_state()

    response = api_client.post(f"/api/workflows/{workflow_id}/chatkit/session")

    assert response.status_code == 401
    payload = response.json()
    assert payload["detail"]["code"] == "auth.missing_token"


def test_chatkit_session_accepts_handle_route(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workflow-scoped ChatKit sessions should accept workflow handles."""
    monkeypatch.setenv("ORCHEO_CHATKIT_TOKEN_SIGNING_KEY", "workflow-session-key")
    reset_chatkit_token_state()

    response = api_client.post(
        "/api/workflows",
        json={
            "name": "Handle Session Workflow",
            "handle": "handle-session-workflow",
            "actor": "tester",
        },
    )
    assert response.status_code == 201

    session_response = api_client.post(
        "/api/workflows/handle-session-workflow/chatkit/session"
    )

    assert session_response.status_code == 200
    payload = session_response.json()
    assert payload["client_secret"]
