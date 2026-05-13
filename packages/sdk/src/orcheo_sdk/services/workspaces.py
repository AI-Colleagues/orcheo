"""Workspace management operations.

Pure business logic for workspace operations, shared by CLI and MCP interfaces.
"""

from __future__ import annotations
from typing import Any
from orcheo_sdk.cli.http import ApiClient


def create_workspace_data(
    client: ApiClient,
    *,
    slug: str,
    name: str,
    owner_user_id: str,
    quotas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a workspace via the admin API."""
    payload: dict[str, Any] = {
        "slug": slug,
        "name": name,
        "owner_user_id": owner_user_id,
    }
    if quotas is not None:
        payload["quotas"] = quotas
    return client.post("/api/admin/workspaces", json_body=payload)


def list_workspaces_data(
    client: ApiClient, *, include_inactive: bool = False
) -> dict[str, Any]:
    """List workspaces visible to the operator."""
    params = {"include_inactive": "true"} if include_inactive else None
    return client.get("/api/admin/workspaces", params=params)


def deactivate_workspace_data(client: ApiClient, workspace_id: str) -> dict[str, Any]:
    """Deactivate a workspace by setting its status to suspended."""
    return client.patch(
        f"/api/admin/workspaces/{workspace_id}/status",
        json_body={"status": "suspended"},
    )


def reactivate_workspace_data(client: ApiClient, workspace_id: str) -> dict[str, Any]:
    """Mark a previously deactivated workspace as active."""
    return client.patch(
        f"/api/admin/workspaces/{workspace_id}/status",
        json_body={"status": "active"},
    )


def delete_workspace_data(
    client: ApiClient, workspace_id: str
) -> dict[str, Any] | None:
    """Hard-delete a workspace."""
    return client.delete(f"/api/admin/workspaces/{workspace_id}")


def purge_deleted_workspaces_data(
    client: ApiClient,
    *,
    retention_days: int = 30,
) -> dict[str, Any] | None:
    """Purge soft-deleted workspaces whose retention window expired."""
    return client.post(
        "/api/admin/workspaces/purge-deleted",
        params={"retention_days": retention_days},
    )


def list_workspace_audit_events_data(
    client: ApiClient,
    workspace_id: str,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Return the audit events recorded for a workspace."""
    return client.get(
        f"/api/admin/workspaces/{workspace_id}/audit-events",
        params={"limit": str(limit)},
    )


def invite_workspace_member_data(
    client: ApiClient,
    *,
    slug: str,
    user_id: str,
    role: str,
) -> dict[str, Any]:
    """Add a member to a workspace."""
    return client.post(
        f"/api/workspaces/{slug}/members",
        json_body={"user_id": user_id, "role": role},
    )


def list_my_workspaces_data(client: ApiClient) -> dict[str, Any]:
    """Return memberships visible to the calling principal."""
    return client.get("/api/workspaces/me")
