"""Restricted-mode guard for MCP server connections.

In restricted definition mode, uploaded workflows are untrusted. An
``mcp_servers`` connection that uses the ``stdio`` transport launches a local
subprocess (``command`` + ``args``) on the worker via
``langchain_mcp_adapters`` — an arbitrary command-execution vector reachable
purely from JSON-literal node config. This guard rejects those connections
before the MCP client is constructed.

Remote transports (``sse`` / ``streamable_http`` / ``websocket``) do not spawn
local processes, so they are left to the node's normal networking behaviour.
"""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from orcheo.graph.ir.definition_mode import is_restricted_mode


# The only MCP transport that launches a local subprocess on the host.
_LOCAL_SPAWN_TRANSPORT = "stdio"


class RestrictedModeMcpError(RuntimeError):
    """Raised when a workflow requests a local (stdio) MCP server in restricted mode."""


def reject_local_mcp_servers_in_restricted_mode(
    mcp_servers: Mapping[str, Any] | None,
    *,
    node_name: str,
) -> None:
    """Block local-subprocess MCP servers when definition mode is restricted.

    Args:
        mcp_servers: The node's ``mcp_servers`` connection mapping.
        node_name: Node id used in the error message.

    Raises:
        RestrictedModeMcpError: When restricted mode is active and any connection
            uses the local ``stdio`` transport (or carries a ``command`` with no
            explicit remote transport).
    """
    if not mcp_servers or not is_restricted_mode():
        return
    for server_name, connection in mcp_servers.items():
        if not isinstance(connection, Mapping):
            continue
        transport = connection.get("transport")
        spawns_local_process = transport == _LOCAL_SPAWN_TRANSPORT or (
            transport is None and "command" in connection
        )
        if spawns_local_process:
            msg = (
                f"Node '{node_name}': MCP server '{server_name}' uses the local "
                "'stdio' transport, which launches a subprocess on the host. "
                "Local MCP servers are not permitted in restricted mode; use a "
                "remote MCP transport (sse/streamable_http) instead."
            )
            raise RestrictedModeMcpError(msg)


__all__ = [
    "RestrictedModeMcpError",
    "reject_local_mcp_servers_in_restricted_mode",
]
