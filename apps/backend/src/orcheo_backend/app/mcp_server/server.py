"""Construct and run the Orcheo MCP server hosted by the backend."""

from __future__ import annotations
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.http import StarletteWithLifespan
from orcheo_backend.app.mcp_server.account_tools import register_account_tools
from orcheo_backend.app.mcp_server.app_tools import register_app_tools
from orcheo_backend.app.mcp_server.catalog_tools import register_catalog_tools
from orcheo_backend.app.mcp_server.run_tools import register_run_tools
from orcheo_backend.app.mcp_server.scopes import OAuthScopeMiddleware
from orcheo_backend.app.mcp_server.workflow_tools import register_workflow_tools


MCP_ENABLED_ENV_VAR = "ORCHEO_MCP_ENABLED"
MCP_PATH = "/api/mcp"
DEFAULT_MCP_ENABLED = True

_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_FALSY_VALUES = {"0", "false", "no", "off"}

_INSTRUCTIONS = """\
Remote control for a running Orcheo server. Workflows are LangGraph Python \
scripts: inspect them with list_workflows/get_workflow/download_workflow, \
change them with upload_workflow, then run_workflow and poll get_run or \
get_run_trace for results. Workflow arguments accept an ID or a handle. \
Every tool acts as the authenticated caller; pass `workspace` to target a \
workspace other than the default one. Secrets are referenced in workflows as \
[[credential_name]]. To add or change one, call open_credential_form: the user \
enters the secret in a form you never see. Never ask for secrets in chat. \
show_workflow_diagram renders a workflow's graph."""


def mcp_enabled() -> bool:
    """Return True unless ``ORCHEO_MCP_ENABLED`` explicitly disables the server."""
    value = os.getenv(MCP_ENABLED_ENV_VAR)
    if value is None:
        return DEFAULT_MCP_ENABLED
    normalized = value.strip().lower()
    if normalized in _TRUTHY_VALUES:
        return True
    if normalized in _FALSY_VALUES:
        return False
    return DEFAULT_MCP_ENABLED


def build_mcp_server() -> FastMCP:
    """Return a FastMCP server with every Orcheo tool registered."""
    server = FastMCP(name="orcheo", instructions=_INSTRUCTIONS)
    server.add_middleware(OAuthScopeMiddleware(server))
    register_workflow_tools(server)
    register_run_tools(server)
    register_catalog_tools(server)
    register_account_tools(server)
    register_app_tools(server)
    return server


def build_mcp_http_app(server: FastMCP) -> StarletteWithLifespan:
    """Return the Streamable HTTP app serving ``server`` at ``/api/mcp``.

    Authentication is left to the backend route in front of it (see
    ``routers/mcp.py``), which reuses Orcheo's own auth modes and hands the
    resolved identity to tools.
    """
    return server.http_app(
        path=MCP_PATH,
        # Stateless JSON responses let any backend replica answer any request,
        # so the endpoint works behind the round-robin proxy without sticky
        # sessions.
        stateless_http=True,
        json_response=True,
        # Callers authenticate with bearer tokens and the backend is served
        # under arbitrary public hostnames, so host/origin pinning (aimed at
        # unauthenticated localhost servers) would only reject valid traffic.
        host_origin_protection=False,
    )


@asynccontextmanager
async def mcp_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Serve MCP requests on ``app`` while the context is open, when enabled."""
    app.state.mcp_http_app = None
    if not mcp_enabled():
        yield
        return
    http_app = build_mcp_http_app(build_mcp_server())
    async with http_app.lifespan(http_app):
        app.state.mcp_http_app = http_app
        try:
            yield
        finally:
            app.state.mcp_http_app = None


__all__ = [
    "MCP_ENABLED_ENV_VAR",
    "MCP_PATH",
    "build_mcp_http_app",
    "build_mcp_server",
    "mcp_enabled",
    "mcp_lifespan",
]
