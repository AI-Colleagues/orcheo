"""Dev cookie authentication paths and HTTP error branches coverage."""

from __future__ import annotations
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocket
from orcheo_backend.app import app
from orcheo_backend.app.authentication import (
    authenticate_request,
    authenticate_websocket,
    reset_authentication_state,
)


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    # Also clear any dev login env from other tests
    for key in (
        "ORCHEO_AUTH_DEV_LOGIN_ENABLED",
        "ORCHEO_AUTH_DEV_COOKIE_NAME",
        "ORCHEO_AUTH_DEV_SCOPES",
        "ORCHEO_AUTH_DEV_WORKSPACE_IDS",
        "ORCHEO_AUTH_MODE",
        "ORCHEO_AUTH_JWT_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_authentication_state()


@pytest.mark.asyncio
async def test_http_dev_cookie_sets_context_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """authenticate_request uses dev cookie and default dev scopes/workspaces."""

    # Enable dev login with a known cookie name and skip setting scopes
    monkeypatch.setenv("ORCHEO_AUTH_DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_AUTH_DEV_COOKIE_NAME", "orcheo_dev_session")
    # Provide workspace IDs to exercise that branch as well
    monkeypatch.setenv("ORCHEO_AUTH_DEV_WORKSPACE_IDS", "ws-1,ws-2")
    reset_authentication_state()

    # Build a minimal Starlette Request with a Cookie header
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", b"orcheo_dev_session=session-value")],
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    request = Request(scope, receive)  # type: ignore[arg-type]

    context = await authenticate_request(request)

    assert context.identity_type == "developer"
    assert context.subject == "session-value"
    # Defaults include workflows and vault scopes
    assert "workflows:read" in context.scopes
    assert "vault:write" in context.scopes
    # Workspace IDs picked from env
    assert context.workspace_ids == frozenset({"ws-1", "ws-2"})
    # Request state is set
    assert request.state.auth is context


@pytest.mark.asyncio
async def test_websocket_dev_cookie_authenticates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """authenticate_websocket accepts dev cookie when no token is provided."""

    monkeypatch.setenv("ORCHEO_AUTH_DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_AUTH_DEV_COOKIE_NAME", "orcheo_dev_session")
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()

    websocket = Mock(spec=WebSocket)
    websocket.headers = {}
    websocket.query_params = {}
    websocket.cookies = {"orcheo_dev_session": "tester"}
    websocket.client = Mock(host="127.0.0.1")
    websocket.state = Mock()
    websocket.close = AsyncMock()

    context = await authenticate_websocket(websocket)

    assert context.identity_type == "developer"
    assert websocket.state.auth is context


@pytest.mark.asyncio
async def test_http_invalid_authorization_scheme_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid Authorization scheme triggers failure path with 401."""

    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"authorization", b"Basic dXNlcjpwYXNz")],
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    request = Request(scope, receive)  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        await authenticate_request(request)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "auth.invalid_scheme"


def test_dev_logout_returns_404_when_disabled() -> None:
    """Dev logout endpoint returns 404 when dev login is disabled."""

    client = TestClient(app)
    response = client.post("/api/auth/dev/logout")

    assert response.status_code == 404
    assert response.json()["detail"]["message"].startswith(
        "Developer login is disabled"
    )
