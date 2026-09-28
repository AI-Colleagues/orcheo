"""MCP tools for credentials, workspaces and server information."""

from __future__ import annotations
from typing import Annotated, Any
from fastmcp import FastMCP
from pydantic import Field
from orcheo_backend.app.mcp_server._shared import (
    DESTRUCTIVE,
    READ_ONLY,
    WorkspaceArg,
    api_client,
)


OptionalWorkflowArg = Annotated[
    str | None,
    Field(description="Workflow ID or handle the credential is scoped to."),
]


def register_account_tools(server: FastMCP) -> None:
    """Register credential, workspace and system tools on ``server``.

    Credentials are created and updated through the credential form app (see
    ``app_tools``) so secrets never pass through the model.
    """

    @server.tool(annotations=READ_ONLY)
    async def list_credentials(
        workflow: OptionalWorkflowArg = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List vault credentials (metadata only; secrets are never returned)."""
        async with api_client(workspace) as api:
            credentials = await api.get(
                "/api/credentials", params={"workflow_id": workflow}
            )
        return {"credentials": credentials}

    @server.tool(annotations=DESTRUCTIVE)
    async def delete_credential(
        credential_id: Annotated[str, Field(description="Credential ID.")],
        workflow: OptionalWorkflowArg = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Permanently delete a credential from the vault."""
        async with api_client(workspace) as api:
            await api.delete(
                f"/api/credentials/{credential_id}", params={"workflow_id": workflow}
            )
        return {"status": "deleted", "credential_id": credential_id}

    @server.tool(annotations=READ_ONLY)
    async def list_my_workspaces() -> dict[str, Any]:
        """List the workspaces the caller belongs to, with their roles."""
        async with api_client() as api:
            return await api.get("/api/workspaces/me")

    @server.tool(annotations=READ_ONLY)
    async def get_active_workspace(workspace: WorkspaceArg = None) -> dict[str, Any]:
        """Show which workspace tool calls act in, and the caller's role there."""
        async with api_client(workspace) as api:
            return await api.get("/api/workspaces/active")

    @server.tool(annotations=READ_ONLY)
    async def get_server_info() -> dict[str, Any]:
        """Return Orcheo component versions and whether uploads are allowed."""
        async with api_client() as api:
            return await api.get("/api/system/info")


__all__ = ["register_account_tools"]
