"""Enforce the scopes a user approved for an OAuth client on MCP tools.

Each tool declares the scopes it needs through ``requires`` tags. Callers that
signed in through OAuth only see and call the tools their grant covers; other
callers (Studio sessions, service tokens) are authorized by the API routes the
tools proxy to, as before.
"""

from __future__ import annotations
from collections.abc import Sequence
import mcp.types as mt
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult
from orcheo_backend.app.authentication import (
    PREAUTHENTICATED_SCOPE_KEY,
    RequestContext,
    is_oauth_client_context,
)


_SCOPE_TAG_PREFIX = "scope:"


def requires(*scopes: str) -> set[str]:
    """Return the tags declaring that a tool needs every scope in ``scopes``."""
    return {f"{_SCOPE_TAG_PREFIX}{scope}" for scope in scopes}


def required_scopes(tool: Tool) -> frozenset[str]:
    """Return the scopes a tool declared through ``requires``."""
    return frozenset(
        tag.removeprefix(_SCOPE_TAG_PREFIX)
        for tag in tool.tags
        if tag.startswith(_SCOPE_TAG_PREFIX)
    )


def _granted_scopes() -> frozenset[str] | None:
    """Return the caller's OAuth-granted scopes, or None when not an OAuth grant."""
    try:
        request = get_http_request()
    except RuntimeError:
        # No HTTP request (in-process listing): there is no OAuth grant, and
        # the tools themselves refuse to run outside HTTP.
        return None
    context = request.scope.get(PREAUTHENTICATED_SCOPE_KEY)
    if isinstance(context, RequestContext) and is_oauth_client_context(context):
        return context.scopes
    return None


class OAuthScopeMiddleware(Middleware):
    """Hide and refuse tools outside the scopes approved for an OAuth client."""

    def __init__(self, server: FastMCP) -> None:
        """Bind the middleware to the server whose tools it guards."""
        self._server = server

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """List only the tools the caller's grant covers."""
        tools = await call_next(context)
        granted = _granted_scopes()
        if granted is None:
            return tools
        return [tool for tool in tools if required_scopes(tool) <= granted]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Refuse a tool whose scopes the caller's grant does not cover."""
        granted = _granted_scopes()
        if granted is not None:
            name = context.message.name
            tool = await self._server.get_tool(name)
            missing = set() if tool is None else required_scopes(tool) - granted
            if missing:
                raise ToolError(
                    f"This application was not granted {', '.join(sorted(missing))}, "
                    f"which '{name}' needs. Reconnect it and approve that access."
                )
        return await call_next(context)


__all__ = ["OAuthScopeMiddleware", "required_scopes", "requires"]
