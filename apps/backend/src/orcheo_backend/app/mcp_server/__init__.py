"""MCP server exposing Orcheo remote controls from the backend process."""

from orcheo_backend.app.mcp_server.server import (
    MCP_ENABLED_ENV_VAR,
    build_mcp_server,
    mcp_enabled,
    mcp_lifespan,
)


__all__ = [
    "MCP_ENABLED_ENV_VAR",
    "build_mcp_server",
    "mcp_enabled",
    "mcp_lifespan",
]
