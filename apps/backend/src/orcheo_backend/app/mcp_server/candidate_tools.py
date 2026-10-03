"""MCP tools for installing and updating official candidate workflows."""

from __future__ import annotations
from typing import Any
from uuid import UUID
from fastmcp import FastMCP
from orcheo_backend.app.mcp_server._shared import (
    READ_ONLY,
    WRITE,
    WorkflowArg,
    WorkspaceArg,
    api_client,
)
from orcheo_backend.app.mcp_server.scopes import requires


def register_candidate_tools(server: FastMCP) -> None:
    """Expose the server-owned candidate catalog and ingestion operations."""

    @server.tool(annotations=READ_ONLY, tags=requires("workflows:read"))
    async def list_candidates(workspace: WorkspaceArg = None) -> dict[str, Any]:
        """List available official candidate colleagues and release notes."""
        async with api_client(workspace) as api:
            return {"candidates": await api.get("/api/candidates")}

    @server.tool(annotations=WRITE, tags=requires("workflows:write"))
    async def onboard_candidate(
        candidate_id: str,
        team_id: UUID | None = None,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Install a candidate, or append a version when already installed."""
        async with api_client(workspace) as api:
            return await api.post(
                "/api/candidates/onboard",
                json_body={
                    "id": candidate_id,
                    "team_id": str(team_id) if team_id else None,
                },
            )

    @server.tool(annotations=WRITE, tags=requires("workflows:write"))
    async def update_candidate_workflow(
        workflow: WorkflowArg,
        candidate_id: str,
        workspace: WorkspaceArg = None,
    ) -> dict[str, Any]:
        """Upgrade an installed candidate while preserving user configuration."""
        async with api_client(workspace) as api:
            record = await api.get(f"/api/workflows/{workflow}")
            return await api.post(
                "/api/candidates/update",
                json_body={"workflow_id": record["id"], "candidate_id": candidate_id},
            )
