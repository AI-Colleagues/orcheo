"""Schemas for workspace admin and member endpoints."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from orcheo.workspace import Role, WorkspaceQuotas, WorkspaceStatus


__all__ = [
    "ActiveWorkspaceResponse",
    "MembershipCreateRequest",
    "MembershipResponse",
    "MembershipRoleUpdateRequest",
    "MeMembershipsResponse",
    "WorkspaceAuditEventListResponse",
    "WorkspaceAuditEventResponse",
    "WorkspaceCreateRequest",
    "WorkspaceListResponse",
    "WorkspaceResponse",
    "WorkspaceStatusUpdateRequest",
]


class WorkspaceCreateRequest(BaseModel):
    """Body of POST /api/admin/workspaces."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    owner_user_id: str = Field(min_length=1)
    quotas: WorkspaceQuotas | None = None


class WorkspaceResponse(BaseModel):
    """Workspace payload returned by admin and member endpoints."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    slug: str
    name: str
    status: WorkspaceStatus
    quotas: WorkspaceQuotas
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkspaceStatusUpdateRequest(BaseModel):
    """Body for PATCH /api/admin/workspaces/{workspace_id}/status."""

    model_config = ConfigDict(extra="forbid")

    status: WorkspaceStatus


class WorkspaceAuditEventResponse(BaseModel):
    """Workspace audit event payload."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    action: str
    actor: str | None
    subject: str | None
    resource_type: str | None
    resource_id: str | None
    details: dict[str, Any]
    created_at: datetime


class WorkspaceAuditEventListResponse(BaseModel):
    """List wrapper for workspace audit events."""

    model_config = ConfigDict(extra="forbid")

    audit_events: list[WorkspaceAuditEventResponse]


class WorkspaceListResponse(BaseModel):
    """List wrapper for admin workspace queries."""

    model_config = ConfigDict(extra="forbid")

    workspaces: list[WorkspaceResponse]


class ActiveWorkspaceResponse(BaseModel):
    """Read-only current workspace summary for UI indicators."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    tenant_id: UUID
    slug: str
    name: str
    role: Role


class MembershipResponse(BaseModel):
    """Workspace membership payload."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    user_id: str
    role: Role
    created_at: datetime


class MembershipCreateRequest(BaseModel):
    """Body for POST /api/workspaces/{slug}/members."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    role: Role = Role.EDITOR


class MembershipRoleUpdateRequest(BaseModel):
    """Body for PATCH /api/workspaces/{slug}/members/{user_id}."""

    model_config = ConfigDict(extra="forbid")

    role: Role


class _MeMembershipEntry(BaseModel):
    """Single membership entry returned from /api/workspaces/me."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    slug: str
    name: str
    role: Role
    status: WorkspaceStatus


class MeMembershipsResponse(BaseModel):
    """Response wrapper for /api/workspaces/me."""

    model_config = ConfigDict(extra="forbid")

    memberships: list[_MeMembershipEntry]
