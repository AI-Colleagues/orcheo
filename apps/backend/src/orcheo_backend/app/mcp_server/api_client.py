"""In-process client that lets MCP tools call the backend's own REST API.

MCP tools deliberately go through the public ``/api`` routes instead of the
repositories: authentication, workspace resolution, role checks, quotas and
validation then behave exactly as they do for the CLI and Studio. Requests are
dispatched straight into the running ASGI app, so nothing leaves the process.
"""

from __future__ import annotations
import json
from collections.abc import Mapping
from typing import Any
import httpx
from fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send
from orcheo_backend.app.authentication import PREAUTHENTICATED_SCOPE_KEY
from orcheo_backend.app.workspace.dependencies import WORKSPACE_HEADER


# Headers copied from the MCP HTTP request onto each in-process API call. The
# identity itself travels in the ASGI scope (see ``_carry_authentication``);
# forwarding credentials keeps routes that read them directly working. The
# ``X-Forwarded-*`` family keeps absolute URLs (e.g. share links) pointing at
# the public origin when the backend sits behind a proxy.
_FORWARDED_HEADERS = (
    "authorization",
    "cookie",
    WORKSPACE_HEADER.lower(),
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
)
_DEFAULT_TIMEOUT_SECONDS = 60.0
# Set by the ``/api/mcp`` route: FastMCP's own Starlette app replaces
# ``scope["app"]``, so the backend app is recorded under this key.
API_APP_SCOPE_KEY = "orcheo.api_app"


class McpApiError(ToolError):
    """Raised when an in-process API call returns an error status."""

    def __init__(self, method: str, path: str, status_code: int, detail: Any) -> None:
        """Record the failing call and render a message the MCP client can show."""
        self.method = method
        self.path = path
        self.status_code = status_code
        self.detail = detail
        super().__init__(
            f"{method} {path} failed with HTTP {status_code}: {_render_detail(detail)}"
        )


def _render_detail(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, Mapping) and isinstance(detail.get("message"), str):
        return str(detail["message"])
    # Workspace quota and rate-limit errors nest theirs under "error".
    error = detail.get("error") if isinstance(detail, Mapping) else None
    if isinstance(error, Mapping) and isinstance(error.get("message"), str):
        return str(error["message"])
    return json.dumps(detail, default=str)


def _carry_authentication(app: ASGIApp, context: object) -> ASGIApp:
    """Reuse the MCP request's authenticated identity for in-process calls.

    The token was validated (and rate limited) once at ``/api/mcp``, so the
    proxied routes take the resolved context from the ASGI scope instead of
    authenticating the same token again.
    """
    if context is None:
        return app

    async def _app(scope: Scope, receive: Receive, send: Send) -> None:
        await app({**scope, PREAUTHENTICATED_SCOPE_KEY: context}, receive, send)

    return _app


class InProcessApiClient:
    """Async JSON client bound to the MCP caller's credentials."""

    def __init__(
        self,
        request: Request,
        *,
        workspace: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Bind the client to the app and caller identity of ``request``.

        Args:
            request: The HTTP request that carried the MCP message.
            workspace: Optional workspace slug overriding the caller's
                ``X-Orcheo-Workspace`` header for these calls.
            timeout: Per-request timeout in seconds.
        """
        headers = {
            name: value
            for name in _FORWARDED_HEADERS
            if (value := request.headers.get(name))
        }
        if workspace is not None and workspace.strip():
            headers[WORKSPACE_HEADER.lower()] = workspace.strip()
        # Preserve the caller's address so IP-based auth rate limits and audit
        # logging see the real client rather than a loopback placeholder.
        peer = request.client
        transport = httpx.ASGITransport(
            app=_carry_authentication(
                request.scope.get(API_APP_SCOPE_KEY, request.scope["app"]),
                request.scope.get(PREAUTHENTICATED_SCOPE_KEY),
            ),
            client=(peer.host, peer.port) if peer else ("127.0.0.1", 0),
        )
        self._client = httpx.AsyncClient(
            transport=transport,
            base_url=f"{request.url.scheme}://{request.url.netloc}",
            headers=headers,
            timeout=timeout,
        )

    async def __aenter__(self) -> InProcessApiClient:
        """Return the client for use in ``async with`` blocks."""
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close the underlying transport."""
        await self._client.aclose()

    async def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        """Issue a GET request and return the decoded JSON body."""
        return await self.request("GET", path, params=params)

    async def post(self, path: str, *, json_body: Any = None) -> Any:
        """Issue a POST request and return the decoded JSON body."""
        return await self.request("POST", path, json_body=json_body)

    async def put(self, path: str, *, json_body: Any = None) -> Any:
        """Issue a PUT request and return the decoded JSON body."""
        return await self.request("PUT", path, json_body=json_body)

    async def delete(
        self, path: str, *, params: Mapping[str, Any] | None = None
    ) -> Any:
        """Issue a DELETE request and return the decoded JSON body, if any."""
        return await self.request("DELETE", path, params=params)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        """Send a request and return its JSON body (``None`` when empty).

        Raises:
            McpApiError: If the API responds with a 4xx/5xx status.
        """
        query = (
            {key: value for key, value in params.items() if value is not None}
            if params
            else None
        )
        response = await self._client.request(
            method, path, params=query, json=json_body
        )
        if response.is_error:
            raise McpApiError(method, path, response.status_code, _decode(response))
        return _decode(response)


def _decode(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if response.is_error and isinstance(payload, Mapping) and "detail" in payload:
        return payload["detail"]
    return payload


__all__ = ["API_APP_SCOPE_KEY", "InProcessApiClient", "McpApiError"]
