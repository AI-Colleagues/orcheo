"""HTTP authentication integration tests."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from orcheo_backend.app.authentication import (
    authenticate_request,
    reset_authentication_state,
)
from tests.backend.authentication_test_utils import (
    _setup_service_token,
    _install_test_authenticator,
    create_test_client,
    reset_auth_state,
)


def _client() -> TestClient:
    """Create a fresh TestClient for each request assertion."""

    return create_test_client()


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


def test_requests_allowed_when_auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requests succeed when no authentication configuration is provided."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    client = _client()
    response = client.get("/api/workflows")
    assert response.status_code == 200


def test_service_token_required_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Authorization header yields 401 when service tokens are configured."""

    _setup_service_token(monkeypatch, "secret-token")
    reset_authentication_state()

    client = _client()
    response = client.get("/api/workflows")

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"
    detail = response.json()["detail"]
    assert detail["code"] == "auth.missing_token"


def test_valid_service_token_allows_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Providing a valid service token authorizes the request."""

    _setup_service_token(monkeypatch, "ci-token", identifier="ci")
    reset_authentication_state()

    client = _client()
    response = client.get(
        "/api/workflows",
        headers={"Authorization": "Bearer ci-token"},
    )

    assert response.status_code == 200


def test_invalid_service_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Incorrect service tokens result in a 401 response."""

    _setup_service_token(monkeypatch, "ci-token", identifier="ci")
    reset_authentication_state()

    client = _client()
    response = client.get(
        "/api/workflows",
        headers={"Authorization": "Bearer not-valid"},
    )

    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["code"] == "auth.invalid_token"


def test_jwt_secret_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT secrets allow bearer token authentication."""

    secret = "jwt-secret"
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", secret)
    reset_authentication_state()

    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": "tester",
            "scope": "workflows:read",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )

    client = _client()
    response = client.get(
        "/api/workflows",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_jwt_missing_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured JWT secret still enforces bearer tokens."""

    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", "jwt-secret")
    reset_authentication_state()

    client = _client()
    response = client.get("/api/workflows")

    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["code"] == "auth.missing_token"


@pytest.mark.asyncio
async def test_authenticate_request_sets_request_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """authenticate_request attaches the resolved context to the request state."""

    _setup_service_token(
        monkeypatch,
        "token-123",
        identifier="ci",
        scopes=["workflows:read"],
        workspace_ids=["ws-1"],
    )
    _install_test_authenticator(monkeypatch)
    reset_authentication_state()

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"authorization", b"Bearer token-123")],
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    request = Request(scope, receive)  # type: ignore[arg-type]

    context = await authenticate_request(request)

    assert context.identity_type == "service"
    assert "workflows:read" in context.scopes
    assert request.state.auth is context
