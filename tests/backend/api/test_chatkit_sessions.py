from __future__ import annotations
import jwt
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.chatkit_tokens import reset_chatkit_token_state
from .shared import create_workflow_with_version
from tests.backend.authentication_test_utils import _setup_service_token


def test_chatkit_session_returns_configured_secret(
    monkeypatch: pytest.MonkeyPatch, api_client: TestClient
) -> None:
    """The ChatKit session endpoint issues a signed token with metadata."""

    _setup_service_token(
        monkeypatch, "session-token", identifier="cli", scopes=["chatkit:session"]
    )
    monkeypatch.setenv("ORCHEO_CHATKIT_TOKEN_SIGNING_KEY", "api-signing-key")
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()
    reset_chatkit_token_state()

    response = api_client.post(
        "/api/chatkit/session",
        headers={"Authorization": "Bearer session-token"},
        json={"user": {"id": "tester"}, "assistant": {"id": "orcheo"}},
    )

    assert response.status_code == status.HTTP_200_OK
    token = response.json()["client_secret"]
    decoded = jwt.decode(
        token,
        "api-signing-key",
        algorithms=["HS256"],
        audience="chatkit",
        issuer="orcheo.chatkit",
    )
    assert decoded["chatkit"]["identity_type"] == "service"


def test_chatkit_session_prefers_workflow_specific_secret(
    monkeypatch: pytest.MonkeyPatch, api_client: TestClient
) -> None:
    """Workflow identifiers should be embedded within the signed token."""

    workflow_id, _ = create_workflow_with_version(api_client)
    _setup_service_token(
        monkeypatch, "session-token", identifier="cli", scopes=["chatkit:session"]
    )
    monkeypatch.setenv("ORCHEO_CHATKIT_TOKEN_SIGNING_KEY", "api-signing-key")
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()
    reset_chatkit_token_state()

    response = api_client.post(
        "/api/chatkit/session",
        headers={"Authorization": "Bearer session-token"},
        json={"workflowId": workflow_id, "currentClientSecret": None},
    )

    assert response.status_code == status.HTTP_200_OK
    token = response.json()["client_secret"]
    decoded = jwt.decode(
        token,
        "api-signing-key",
        algorithms=["HS256"],
        audience="chatkit",
        issuer="orcheo.chatkit",
    )
    assert decoded["chatkit"]["workflow_id"] == workflow_id


def test_chatkit_session_missing_secret_returns_service_unavailable(
    monkeypatch: pytest.MonkeyPatch, api_client: TestClient
) -> None:
    """Missing ChatKit signing key surfaces a configuration error."""

    _setup_service_token(
        monkeypatch, "session-token", identifier="cli", scopes=["chatkit:session"]
    )
    monkeypatch.setenv("ORCHEO_CHATKIT_TOKEN_SIGNING_KEY", "")
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()
    reset_chatkit_token_state()

    response = api_client.post(
        "/api/chatkit/session",
        headers={"Authorization": "Bearer session-token"},
        json={},
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    detail = response.json()["detail"]
    assert "signing key" in detail["message"].lower()
