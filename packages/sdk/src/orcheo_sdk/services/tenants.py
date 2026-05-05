"""Tenant management operations.

Pure business logic for tenant operations, shared by CLI and MCP interfaces.
"""

from __future__ import annotations
from typing import Any
from orcheo_sdk.cli.http import ApiClient


def create_tenant_data(
    client: ApiClient,
    *,
    slug: str,
    name: str,
    owner_user_id: str,
    quotas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a tenant via the admin API."""
    payload: dict[str, Any] = {
        "slug": slug,
        "name": name,
        "owner_user_id": owner_user_id,
    }
    if quotas is not None:
        payload["quotas"] = quotas
    return client.post("/api/admin/tenants", json_body=payload)


def list_tenants_data(
    client: ApiClient, *, include_inactive: bool = False
) -> dict[str, Any]:
    """List tenants visible to the operator."""
    params = {"include_inactive": "true"} if include_inactive else None
    return client.get("/api/admin/tenants", params=params)


def deactivate_tenant_data(client: ApiClient, tenant_id: str) -> dict[str, Any]:
    """Deactivate a tenant by setting its status to suspended."""
    return client.patch(
        f"/api/admin/tenants/{tenant_id}/status",
        json_body={"status": "suspended"},
    )


def reactivate_tenant_data(client: ApiClient, tenant_id: str) -> dict[str, Any]:
    """Mark a previously deactivated tenant as active."""
    return client.patch(
        f"/api/admin/tenants/{tenant_id}/status",
        json_body={"status": "active"},
    )


def delete_tenant_data(client: ApiClient, tenant_id: str) -> dict[str, Any] | None:
    """Hard-delete a tenant."""
    return client.delete(f"/api/admin/tenants/{tenant_id}")


def purge_deleted_tenants_data(
    client: ApiClient,
    *,
    retention_days: int = 30,
) -> dict[str, Any] | None:
    """Purge soft-deleted tenants whose retention window expired."""
    return client.post(
        "/api/admin/tenants/purge-deleted",
        params={"retention_days": retention_days},
    )


def list_tenant_audit_events_data(
    client: ApiClient,
    tenant_id: str,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Return the audit events recorded for a tenant."""
    return client.get(
        f"/api/admin/tenants/{tenant_id}/audit-events",
        params={"limit": str(limit)},
    )


def invite_tenant_member_data(
    client: ApiClient,
    *,
    slug: str,
    user_id: str,
    role: str,
) -> dict[str, Any]:
    """Add a member to a tenant."""
    return client.post(
        f"/api/tenants/{slug}/members",
        json_body={"user_id": user_id, "role": role},
    )


def list_my_tenants_data(client: ApiClient) -> dict[str, Any]:
    """Return memberships visible to the calling principal."""
    return client.get("/api/tenants/me")
