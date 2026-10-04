"""Tests for the backend-hosted MCP server plumbing."""

from __future__ import annotations
from types import SimpleNamespace
from typing import Any
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastmcp.exceptions import ToolError
from orcheo_backend.app.authentication import (
    PREAUTHENTICATED_SCOPE_KEY,
    RequestContext,
)
from orcheo_backend.app.mcp_server import (
    MCP_ENABLED_ENV_VAR,
    mcp_enabled,
    mcp_lifespan,
)
from orcheo_backend.app.mcp_server._shared import api_client
from orcheo_backend.app.mcp_server.api_client import InProcessApiClient, McpApiError
from orcheo_backend.app.routers import mcp as mcp_router
from tests.backend.api.mcp_support import MCP_HEADERS


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, True), ("true", True), (" OFF ", False), ("0", False), ("maybe", True)],
)
def test_mcp_enabled_flag(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    if value is None:
        monkeypatch.delenv(MCP_ENABLED_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(MCP_ENABLED_ENV_VAR, value)

    assert mcp_enabled() is expected


def _mcp_app() -> FastAPI:
    app = FastAPI()
    app.include_router(mcp_router.router, prefix="/api")
    return app


async def _post_initialize(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post(
            "/api/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            headers=MCP_HEADERS,
        )


@pytest.mark.asyncio()
async def test_endpoint_serves_initialize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MCP_ENABLED_ENV_VAR, raising=False)
    app = _mcp_app()
    app.dependency_overrides[mcp_router.authenticate_mcp_request] = lambda: None

    async with mcp_lifespan(app):
        response = await _post_initialize(app)

    assert response.status_code == 200
    server_info = response.json()["result"]["serverInfo"]
    assert server_info["name"] == "orcheo"
    [icon] = server_info["icons"]
    assert icon["mimeType"] == "image/png"
    assert icon["sizes"] == ["64x64"]
    assert icon["src"].startswith("data:image/png;base64,iVBORw0KGgo")
    assert app.state.mcp_http_app is None


@pytest.mark.asyncio()
async def test_endpoint_returns_404_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MCP_ENABLED_ENV_VAR, "false")
    app = _mcp_app()

    # No credentials: a disabled server 404s before asking for sign-in.
    async with mcp_lifespan(app):
        response = await _post_initialize(app)

    assert response.status_code == 404
    assert "disabled" in response.json()["detail"]


def _echo_app() -> FastAPI:
    app = FastAPI()

    @app.get("/echo")
    async def echo(request: Request) -> dict[str, Any]:
        return {
            "headers": dict(request.headers),
            "client": request.client.host if request.client else None,
            "preauthenticated": request.scope.get(PREAUTHENTICATED_SCOPE_KEY)
            is not None,
            "query": dict(request.query_params),
        }

    @app.delete("/empty")
    async def empty() -> Response:
        return Response(status_code=204)

    @app.get("/text")
    async def text() -> PlainTextResponse:
        return PlainTextResponse("plain")

    @app.get("/fail/{kind}")
    async def fail(kind: str) -> JSONResponse:
        details: dict[str, Any] = {
            "string": {"detail": "bad thing"},
            "message": {"detail": {"message": "structured", "code": "x"}},
            "list": {"detail": [{"loc": ["body"], "msg": "invalid"}]},
            "bare": {"error": "no detail key"},
        }
        return JSONResponse(details[kind], status_code=422)

    @app.post("/fail-text")
    async def fail_text() -> PlainTextResponse:
        return PlainTextResponse("upstream exploded", status_code=502)

    return app


def _incoming_request(
    app: FastAPI,
    *,
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("203.0.113.9", 4321),
) -> Request:
    raw_headers = [
        (name.lower().encode(), value.encode())
        for name, value in (headers or {}).items()
    ] + [(b"host", b"orcheo.example.com")]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/api/mcp",
            "raw_path": b"/api/mcp",
            "query_string": b"",
            "root_path": "",
            "headers": raw_headers,
            "client": client,
            "server": ("orcheo.example.com", 443),
            "app": app,
        }
    )


@pytest.mark.asyncio()
async def test_client_forwards_caller_identity() -> None:
    request = _incoming_request(
        _echo_app(),
        headers={
            "Authorization": "Bearer token-123",
            "X-Orcheo-Workspace": "from-header",
            "X-Forwarded-Proto": "https",
            "X-Unrelated": "dropped",
        },
    )

    async with InProcessApiClient(request) as api:
        default = await api.get("/echo", params={"a": 1, "skip": None})
    async with InProcessApiClient(request, workspace=" override ") as api:
        override = await api.get("/echo")
    async with InProcessApiClient(request, workspace=" ") as api:
        blank = await api.get("/echo")

    assert default["headers"]["authorization"] == "Bearer token-123"
    assert default["headers"]["x-orcheo-workspace"] == "from-header"
    assert default["headers"]["x-forwarded-proto"] == "https"
    assert default["headers"]["host"] == "orcheo.example.com"
    assert "x-unrelated" not in default["headers"]
    assert default["client"] == "203.0.113.9"
    assert default["query"] == {"a": "1"}
    assert default["preauthenticated"] is False
    assert override["headers"]["x-orcheo-workspace"] == "override"
    assert blank["headers"]["x-orcheo-workspace"] == "from-header"


@pytest.mark.asyncio()
async def test_client_decodes_bodies_and_errors() -> None:
    request = _incoming_request(_echo_app(), client=None)

    async with InProcessApiClient(request) as api:
        assert (await api.get("/echo"))["client"] == "127.0.0.1"
        assert await api.delete("/empty") is None
        assert await api.get("/text") == "plain"
        errors = {}
        for kind in ("string", "message", "list", "bare"):
            with pytest.raises(McpApiError) as excinfo:
                await api.get(f"/fail/{kind}")
            errors[kind] = excinfo.value
        with pytest.raises(McpApiError) as text_error:
            await api.post("/fail-text", json_body={})
        with pytest.raises(McpApiError) as put_error:
            await api.put("/text", json_body={})

    assert str(errors["string"]) == "GET /fail/string failed with HTTP 422: bad thing"
    assert str(errors["message"]).endswith("HTTP 422: structured")
    assert errors["message"].detail == {"message": "structured", "code": "x"}
    assert '"msg": "invalid"' in str(errors["list"])
    assert '"error": "no detail key"' in str(errors["bare"])
    assert str(text_error.value).endswith("HTTP 502: upstream exploded")
    assert put_error.value.method == "PUT"


def test_api_client_requires_http_transport() -> None:
    with pytest.raises(ToolError, match="only available over HTTP"):
        api_client()


@pytest.mark.asyncio()
async def test_client_carries_the_gate_identity() -> None:
    request = _incoming_request(_echo_app())
    request.scope[PREAUTHENTICATED_SCOPE_KEY] = RequestContext.anonymous()

    async with InProcessApiClient(request) as api:
        echoed = await api.get("/echo")

    assert echoed["preauthenticated"] is True


@pytest.mark.asyncio()
async def test_every_tool_declares_its_oauth_scopes() -> None:
    from orcheo_backend.app.mcp_server import build_mcp_server
    from orcheo_backend.app.mcp_server.scopes import required_scopes

    tools = await build_mcp_server().list_tools()
    unscoped = {tool.name for tool in tools if not required_scopes(tool)}

    # Only catalog, workspace discovery and server capability tools may skip scopes.
    assert unscoped == {
        "describe_component",
        "get_active_workspace",
        "get_server_info",
        "get_server_readiness",
        "get_server_features",
        "list_server_plugins",
        "list_components",
        "list_my_workspaces",
    }


@pytest.mark.asyncio
async def test_client_uploads_multipart_bundle_and_reports_errors() -> None:
    app = FastAPI()

    @app.post("/upload")
    async def upload(request: Request) -> dict[str, Any]:
        async with request.form() as form:
            bundle = form["bundle"]
            return {
                "filename": bundle.filename,
                "content_type": bundle.content_type,
                "content": (await bundle.read()).decode(),
            }

    async with InProcessApiClient(_incoming_request(app)) as api:
        result = await api.upload("/upload", bundle=b"test bundle")
        with pytest.raises(McpApiError) as excinfo:
            await api.upload("/missing", bundle=b"test bundle")

    assert result == {
        "filename": "bundle.zip",
        "content_type": "application/zip",
        "content": "test bundle",
    }
    assert excinfo.value.status_code == 404
    assert excinfo.value.method == "POST"
    assert excinfo.value.detail == "Not Found"
