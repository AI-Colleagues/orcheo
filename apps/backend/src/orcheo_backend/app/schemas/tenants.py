"""Schemas for tenant admin and member endpoints."""

from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from orcheo.tenancy import Role, TenantQuotas, TenantStatus


__all__ = [
    "ActiveTenantResponse",
    "MembershipCreateRequest",
    "MembershipResponse",
    "MembershipRoleUpdateRequest",
    "MeMembershipsResponse",
    "TenantCreateRequest",
    "TenantListResponse",
    "TenantResponse",
    "TenantStatusUpdateRequest",
]


class TenantCreateRequest(BaseModel):
    """Body of POST /api/admin/tenants."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    owner_user_id: str = Field(min_length=1)
    quotas: TenantQuotas | None = None


class TenantResponse(BaseModel):
    """Tenant payload returned by admin and member endpoints."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    slug: str
    name: str
    status: TenantStatus
    quotas: TenantQuotas
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TenantStatusUpdateRequest(BaseModel):
    """Body for PATCH /api/admin/tenants/{tenant_id}/status."""

    model_config = ConfigDict(extra="forbid")

    status: TenantStatus


class TenantListResponse(BaseModel):
    """List wrapper for admin tenant queries."""

    model_config = ConfigDict(extra="forbid")

    tenants: list[TenantResponse]


class ActiveTenantResponse(BaseModel):
    """Read-only current tenant summary for UI indicators."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    slug: str
    name: str
    role: Role


class MembershipResponse(BaseModel):
    """Tenant membership payload."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    user_id: str
    role: Role
    created_at: datetime


class MembershipCreateRequest(BaseModel):
    """Body for POST /api/tenants/{slug}/members."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    role: Role = Role.EDITOR


class MembershipRoleUpdateRequest(BaseModel):
    """Body for PATCH /api/tenants/{slug}/members/{user_id}."""

    model_config = ConfigDict(extra="forbid")

    role: Role


class _MeMembershipEntry(BaseModel):
    """Single membership entry returned from /api/tenants/me."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    slug: str
    name: str
    role: Role
    status: TenantStatus


class MeMembershipsResponse(BaseModel):
    """Response wrapper for /api/tenants/me."""

    model_config = ConfigDict(extra="forbid")

    memberships: list[_MeMembershipEntry]
