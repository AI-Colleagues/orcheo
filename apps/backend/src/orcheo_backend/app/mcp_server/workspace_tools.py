"""MCP workspace administration through the role-checked REST routes."""

from __future__ import annotations
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID
from fastmcp import FastMCP
from pydantic import Field
from orcheo.workspace import Role, WorkspaceQuotas, WorkspaceStatus
from orcheo_backend.app.mcp_server._shared import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE,
    WorkspaceArg,
    api_client,
)
from orcheo_backend.app.mcp_server.api_client import InProcessApiClient
from orcheo_backend.app.mcp_server.scopes import requires


async def _workspace_path(api: InProcessApiClient) -> str:
    """Resolve the slug using the same workspace context as the following call."""
    active = await api.get("/api/workspaces/active")
    return f"/api/workspaces/{quote(active['slug'], safe='')}"


def register_workspace_tools(server: FastMCP) -> None:
    """Register lifecycle, membership, invitation and audit tools."""
    _register_lifecycle_tools(server)
    _register_member_tools(server)
    _register_invitation_tools(server)


def _register_lifecycle_tools(server: FastMCP) -> None:
    @server.tool(annotations=READ_ONLY, tags=requires("workspaces:read"))
    async def list_workspaces(
        include_inactive: bool = False, workspace: WorkspaceArg = None
    ) -> dict[str, Any]:
        """List workspaces through the admin API; requires admin or owner role."""
        async with api_client(workspace) as api:
            return await api.get(
                "/api/admin/workspaces", params={"include_inactive": include_inactive}
            )

    @server.tool(annotations=READ_ONLY, tags=requires("workspaces:read"))
    async def get_workspace(
        workspace_id: UUID, workspace: WorkspaceArg = None
    ) -> dict[str, Any]:
        """Read workspace metadata and quotas; requires admin or owner role."""
        async with api_client(workspace) as api:
            return await api.get(f"/api/admin/workspaces/{workspace_id}")

    @server.tool(annotations=WRITE, tags=requires("workspaces:write"))
    async def create_workspace(
        slug: str,
        name: str,
        owner_user_id: str | None = None,
        quotas: WorkspaceQuotas | None = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Create an owned workspace; assigning another owner requires admin role.

        Omit owner_user_id to use the self-service route as the caller. The
        workspace argument selects the context authorizing an admin creation.
        """
        payload: dict[str, Any] = {"slug": slug, "name": name}
        if quotas is not None:
            payload["quotas"] = quotas.model_dump(mode="json")
        path = "/api/workspaces"
        if owner_user_id is not None:
            payload["owner_user_id"] = owner_user_id
            path = "/api/admin/workspaces"
        async with api_client(workspace) as api:
            return await api.post(path, json_body=payload)

    @server.tool(annotations=DESTRUCTIVE, tags=requires("workspaces:write"))
    async def update_workspace_status(
        workspace_id: UUID,
        status: WorkspaceStatus,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Activate, suspend or soft-delete a workspace; requires admin role."""
        async with api_client(workspace) as api:
            return await api.request(
                "PATCH",
                f"/api/admin/workspaces/{workspace_id}/status",
                json_body={"status": status.value},
            )

    @server.tool(annotations=DESTRUCTIVE, tags=requires("workspaces:write"))
    async def delete_workspace(
        workspace_id: UUID, workspace: WorkspaceArg = None
    ) -> dict[str, Any]:
        """Permanently delete a workspace and its memberships; requires admin role."""
        async with api_client(workspace) as api:
            await api.delete(f"/api/admin/workspaces/{workspace_id}")
        return {"deleted": True, "workspace_id": str(workspace_id)}

    @server.tool(annotations=DESTRUCTIVE, tags=requires("workspaces:write"))
    async def purge_deleted_workspaces(
        retention_days: Annotated[int, Field(ge=0)] = 30,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Permanently purge soft-deleted workspaces past retention; admin only."""
        async with api_client(workspace) as api:
            await api.request(
                "POST",
                "/api/admin/workspaces/purge-deleted",
                params={"retention_days": retention_days},
            )
        return {"purged": True, "retention_days": retention_days}

    @server.tool(annotations=READ_ONLY, tags=requires("workspaces:read"))
    async def list_workspace_audit_events(
        workspace_id: UUID,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Read a workspace's audit trail; requires admin or owner role."""
        async with api_client(workspace) as api:
            return await api.get(
                f"/api/admin/workspaces/{workspace_id}/audit-events",
                params={"limit": limit},
            )


def _register_member_tools(server: FastMCP) -> None:
    @server.tool(annotations=READ_ONLY, tags=requires("workspaces:read"))
    async def list_workspace_members(workspace: WorkspaceArg = None) -> dict[str, Any]:
        """List members of the selected workspace; requires admin or owner role."""
        async with api_client(workspace) as api:
            path = await _workspace_path(api)
            return {"members": await api.get(f"{path}/members")}

    @server.tool(annotations=WRITE, tags=requires("workspaces:write"))
    async def add_workspace_member(
        user_id: str, role: Role = Role.EDITOR, workspace: WorkspaceArg = None
    ) -> dict[str, Any]:
        """Add a known user to the selected workspace, subject to role checks."""
        async with api_client(workspace) as api:
            path = await _workspace_path(api)
            return await api.post(
                f"{path}/members", json_body={"user_id": user_id, "role": role.value}
            )

    @server.tool(annotations=DESTRUCTIVE, tags=requires("workspaces:write"))
    async def update_workspace_member_role(
        user_id: str, role: Role, workspace: WorkspaceArg = None
    ) -> dict[str, Any]:
        """Change a member's role; requires admin or owner role."""
        async with api_client(workspace) as api:
            path = await _workspace_path(api)
            return await api.request(
                "PATCH",
                f"{path}/members/{quote(user_id, safe='')}",
                json_body={"role": role.value},
            )

    @server.tool(annotations=DESTRUCTIVE, tags=requires("workspaces:write"))
    async def remove_workspace_member(
        user_id: str, workspace: WorkspaceArg = None
    ) -> dict[str, Any]:
        """Remove a member; requires admin or owner role."""
        async with api_client(workspace) as api:
            path = await _workspace_path(api)
            await api.delete(f"{path}/members/{quote(user_id, safe='')}")
        return {"removed": True, "user_id": user_id}


def _register_invitation_tools(server: FastMCP) -> None:
    @server.tool(annotations=READ_ONLY, tags=requires("workspaces:read"))
    async def list_workspace_invitations(
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """List invitation metadata without acceptance tokens; admin only."""
        async with api_client(workspace) as api:
            path = await _workspace_path(api)
            return await api.get(f"{path}/invitations")

    @server.tool(annotations=WRITE, tags=requires("workspaces:write"))
    async def create_workspace_invitation(
        email: str, role: Role = Role.EDITOR, workspace: WorkspaceArg = None
    ) -> dict[str, Any]:
        """Email an invitation to join the selected workspace; admin only.

        The acceptance link is delivered by the backend email service and is
        never returned to the assistant.
        """
        async with api_client(workspace) as api:
            path = await _workspace_path(api)
            return await api.post(
                f"{path}/invitations", json_body={"email": email, "role": role.value}
            )

    @server.tool(annotations=DESTRUCTIVE, tags=requires("workspaces:write"))
    async def revoke_workspace_invitation(
        invitation_id: UUID, workspace: WorkspaceArg = None
    ) -> dict[str, Any]:
        """Revoke a pending workspace invitation; requires admin or owner role."""
        async with api_client(workspace) as api:
            path = await _workspace_path(api)
            return await api.delete(f"{path}/invitations/{invitation_id}")
