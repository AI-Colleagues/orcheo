"""Tenant identity models used across Orcheo subsystems."""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import Field, field_validator
from orcheo.models.base import OrcheoBaseModel, _utcnow


__all__ = [
    "DEFAULT_TENANT_SLUG",
    "Role",
    "TenantAuditEvent",
    "Tenant",
    "TenantContext",
    "TenantMembership",
    "TenantQuotas",
    "TenantStatus",
    "normalize_slug",
]


DEFAULT_TENANT_SLUG = "default"

_ROLE_RANK = {
    "viewer": 0,
    "editor": 1,
    "admin": 2,
    "owner": 3,
}


class Role(str, Enum):
    """Roles that a principal can have inside a tenant."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        """Numeric rank used for hierarchy comparisons."""
        return _ROLE_RANK[self.value]

    def includes(self, other: Role) -> bool:
        """Return True when this role implies `other`."""
        return self.rank >= other.rank


class TenantStatus(str, Enum):
    """Lifecycle states for a tenant."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


def normalize_slug(value: str) -> str:
    """Normalize a tenant slug to a stable URL-safe form."""
    candidate = value.strip().lower()
    if not candidate:
        msg = "Tenant slug must not be empty."
        raise ValueError(msg)
    if not all(ch.isalnum() or ch in {"-", "_"} for ch in candidate):
        msg = (
            "Tenant slug must contain only alphanumeric characters, "
            "hyphens, or underscores."
        )
        raise ValueError(msg)
    return candidate


class TenantQuotas(OrcheoBaseModel):
    """Per-tenant quota configuration with sensible defaults."""

    max_workflows: int = Field(default=100, ge=1)
    max_concurrent_runs: int = Field(default=25, ge=1)
    max_credentials: int = Field(default=200, ge=1)
    max_storage_rows: int = Field(default=1_000_000, ge=1)


class TenantAuditEvent(OrcheoBaseModel):
    """Single tenant audit event describing sensitive activity."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    action: str
    actor: str | None = None
    subject: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class Tenant(OrcheoBaseModel):
    """Tenant record describing an isolated workspace."""

    id: UUID = Field(default_factory=uuid4)
    slug: str
    name: str
    status: TenantStatus = TenantStatus.ACTIVE
    quotas: TenantQuotas = Field(default_factory=TenantQuotas)
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("slug", mode="before")
    @classmethod
    def _coerce_slug(cls, value: object) -> str:
        return normalize_slug(str(value))

    @field_validator("name", mode="before")
    @classmethod
    def _coerce_name(cls, value: object) -> str:
        candidate = str(value).strip()
        if not candidate:
            msg = "Tenant name must not be empty."
            raise ValueError(msg)
        return candidate


class TenantMembership(OrcheoBaseModel):
    """Mapping between a principal and a tenant role."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    user_id: str
    role: Role = Role.VIEWER
    created_at: datetime = Field(default_factory=_utcnow)


class TenantContext(OrcheoBaseModel):
    """Tenancy details propagated through requests, runtime, and tasks."""

    tenant_id: UUID
    tenant_slug: str
    user_id: str
    role: Role
    quotas: TenantQuotas = Field(default_factory=TenantQuotas)

    @field_validator("tenant_slug", mode="before")
    @classmethod
    def _coerce_slug(cls, value: object) -> str:
        return normalize_slug(str(value))

    def has_role(self, required: Role) -> bool:
        """Return True if the context's role meets the required role."""
        return self.role.includes(required)

    def to_headers(self) -> dict[str, str]:
        """Serialize context to header-friendly fields for task envelopes."""
        return {
            "x-orcheo-tenant-id": str(self.tenant_id),
            "x-orcheo-tenant-slug": self.tenant_slug,
            "x-orcheo-user-id": self.user_id,
            "x-orcheo-role": self.role.value,
        }

    @classmethod
    def from_headers(cls, headers: dict[str, Any]) -> TenantContext:
        """Reconstruct context from headers; raises ValueError on missing keys."""
        try:
            tenant_id = UUID(str(headers["x-orcheo-tenant-id"]))
            tenant_slug = str(headers["x-orcheo-tenant-slug"])
            user_id = str(headers["x-orcheo-user-id"])
            role = Role(str(headers["x-orcheo-role"]))
        except KeyError as exc:
            msg = f"Missing tenant header: {exc.args[0]}"
            raise ValueError(msg) from exc
        return cls(
            tenant_id=tenant_id,
            tenant_slug=tenant_slug,
            user_id=user_id,
            role=role,
        )
