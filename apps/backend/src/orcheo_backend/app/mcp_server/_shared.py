"""Helpers shared by the MCP tool modules."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Annotated, Any
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from pydantic import Field
from orcheo_backend.app.authentication import (
    PREAUTHENTICATED_SCOPE_KEY,
    RequestContext,
)
from orcheo_backend.app.mcp_server.api_client import InProcessApiClient


# Actor recorded in audit trails for changes made through MCP, mirroring the
# ``cli`` actor used by the Orcheo CLI.
MCP_ACTOR = "mcp"

WorkspaceArg = Annotated[
    str | None,
    Field(
        description=(
            "Workspace slug to act in. Defaults to the X-Orcheo-Workspace header "
            "sent by the MCP client, then to the caller's default workspace."
        ),
    ),
]
WorkflowArg = Annotated[str, Field(description="Workflow ID (UUID) or handle.")]
VersionArg = Annotated[
    int | None,
    Field(description="Workflow version number. Defaults to the latest version."),
]

READ_ONLY: dict[str, Any] = {"readOnlyHint": True, "openWorldHint": False}
WRITE: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
}
IDEMPOTENT_WRITE: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
}
DESTRUCTIVE: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
}


def api_client(workspace: str | None = None) -> InProcessApiClient:
    """Return an API client acting as the caller of the current MCP request."""
    try:
        request = get_http_request()
    except RuntimeError as exc:
        raise ToolError(
            "Orcheo MCP tools are only available over HTTP transport."
        ) from exc
    return InProcessApiClient(request, workspace=workspace)


def caller_subject() -> str:
    """Return the subject that authenticated the current MCP request."""
    context = get_http_request().scope.get(PREAUTHENTICATED_SCOPE_KEY)
    return context.subject if isinstance(context, RequestContext) else "anonymous"


def version_summary(version: Mapping[str, Any]) -> dict[str, Any]:
    """Drop the bulky graph and diagram payloads from a workflow version."""
    return {
        key: value for key, value in version.items() if key not in {"graph", "mermaid"}
    }


async def fetch_version(
    api: InProcessApiClient, workflow: str, version: int | None
) -> dict[str, Any]:
    """Return a specific workflow version, or the latest when ``version`` is None."""
    if version is not None:
        return await api.get(f"/api/workflows/{workflow}/versions/{version}")
    versions = await api.get(f"/api/workflows/{workflow}/versions")
    if not versions:
        raise ToolError(f"Workflow '{workflow}' has no versions yet.")
    return max(versions, key=lambda entry: entry.get("version", 0))


__all__ = [
    "DESTRUCTIVE",
    "IDEMPOTENT_WRITE",
    "MCP_ACTOR",
    "READ_ONLY",
    "VersionArg",
    "WRITE",
    "WorkflowArg",
    "WorkspaceArg",
    "api_client",
    "caller_subject",
    "fetch_version",
    "version_summary",
]
