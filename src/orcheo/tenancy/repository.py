"""Tenant repository protocol and an in-memory reference implementation."""

from __future__ import annotations
from typing import Protocol
from uuid import UUID
from orcheo.models.base import _utcnow
from orcheo.tenancy.errors import (
    TenantMembershipError,
    TenantNotFoundError,
    TenantSlugConflictError,
)
from orcheo.tenancy.models import (
    Role,
    Tenant,
    TenantAuditEvent,
    TenantMembership,
    TenantStatus,
    normalize_slug,
)


__all__ = [
    "InMemoryTenantRepository",
    "TenantRepository",
]


class TenantRepository(Protocol):
    """Storage protocol for tenants and memberships."""

    def create_tenant(self, tenant: Tenant) -> Tenant:
        """Persist a new tenant; raises on slug conflict."""

    def get_tenant(self, tenant_id: UUID) -> Tenant:
        """Return the tenant identified by `tenant_id`."""

    def get_tenant_by_slug(self, slug: str) -> Tenant:
        """Return the tenant identified by `slug`."""

    def list_tenants(self, *, include_inactive: bool = False) -> list[Tenant]:
        """List tenants, optionally including suspended/deleted ones."""

    def update_status(self, tenant_id: UUID, status: TenantStatus) -> Tenant:
        """Mutate the tenant's lifecycle status and return the updated record."""

    def delete_tenant(self, tenant_id: UUID) -> None:
        """Hard-delete a tenant and cascade its memberships."""

    def add_membership(self, membership: TenantMembership) -> TenantMembership:
        """Persist a new membership; raises on duplicates."""

    def remove_membership(self, tenant_id: UUID, user_id: str) -> None:
        """Remove a membership keyed by `(tenant_id, user_id)`."""

    def update_membership_role(
        self, tenant_id: UUID, user_id: str, role: Role
    ) -> TenantMembership:
        """Change a membership's role and return the updated record."""

    def get_membership(self, tenant_id: UUID, user_id: str) -> TenantMembership:
        """Return the membership identified by `(tenant_id, user_id)`."""

    def list_memberships_for_user(self, user_id: str) -> list[TenantMembership]:
        """Return every membership for a given principal."""

    def list_memberships_for_tenant(self, tenant_id: UUID) -> list[TenantMembership]:
        """Return every membership inside a tenant."""

    def record_audit_event(self, event: TenantAuditEvent) -> TenantAuditEvent:
        """Persist a tenant audit event."""

    def list_audit_events(
        self, tenant_id: UUID, *, limit: int = 100
    ) -> list[TenantAuditEvent]:
        """Return the most recent tenant audit events."""


class InMemoryTenantRepository:
    """In-memory tenant repository used for tests and embedded deployments."""

    def __init__(self) -> None:
        """Initialize empty in-memory storage."""
        self._tenants: dict[UUID, Tenant] = {}
        self._slug_index: dict[str, UUID] = {}
        self._memberships: dict[tuple[UUID, str], TenantMembership] = {}
        self._audit_events: list[TenantAuditEvent] = []

    def create_tenant(self, tenant: Tenant) -> Tenant:
        """Persist a new tenant; raises on slug conflict."""
        slug = normalize_slug(tenant.slug)
        if slug in self._slug_index:
            msg = f"Tenant slug already exists: {slug}"
            raise TenantSlugConflictError(msg)
        self._tenants[tenant.id] = tenant
        self._slug_index[slug] = tenant.id
        return tenant

    def get_tenant(self, tenant_id: UUID) -> Tenant:
        """Return the tenant identified by `tenant_id`."""
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))
        return tenant

    def get_tenant_by_slug(self, slug: str) -> Tenant:
        """Return the tenant identified by `slug`."""
        normalized = normalize_slug(slug)
        tenant_id = self._slug_index.get(normalized)
        if tenant_id is None:
            raise TenantNotFoundError(normalized)
        return self.get_tenant(tenant_id)

    def list_tenants(self, *, include_inactive: bool = False) -> list[Tenant]:
        """List tenants, optionally including suspended/deleted ones."""
        tenants = list(self._tenants.values())
        if include_inactive:
            return sorted(tenants, key=lambda t: t.slug)
        return sorted(
            (t for t in tenants if t.status is TenantStatus.ACTIVE),
            key=lambda t: t.slug,
        )

    def update_status(self, tenant_id: UUID, status: TenantStatus) -> Tenant:
        """Mutate the tenant's status and return the updated record."""
        tenant = self.get_tenant(tenant_id)
        tenant.status = status
        tenant.deleted_at = _utcnow() if status is TenantStatus.DELETED else None
        return tenant

    def delete_tenant(self, tenant_id: UUID) -> None:
        """Hard-delete a tenant and cascade its memberships."""
        tenant = self.get_tenant(tenant_id)
        self._slug_index.pop(tenant.slug, None)
        self._tenants.pop(tenant.id, None)
        for key in list(self._memberships):
            if key[0] == tenant.id:
                self._memberships.pop(key, None)
        self._audit_events = [
            event for event in self._audit_events if event.tenant_id != tenant.id
        ]

    def add_membership(self, membership: TenantMembership) -> TenantMembership:
        """Persist a new membership; raises on duplicates."""
        if membership.tenant_id not in self._tenants:
            raise TenantNotFoundError(str(membership.tenant_id))
        key = (membership.tenant_id, membership.user_id)
        if key in self._memberships:
            msg = (
                f"Membership already exists for user {membership.user_id} in tenant "
                f"{membership.tenant_id}"
            )
            raise TenantMembershipError(msg)
        self._memberships[key] = membership
        return membership

    def remove_membership(self, tenant_id: UUID, user_id: str) -> None:
        """Remove a membership keyed by `(tenant_id, user_id)`."""
        key = (tenant_id, user_id)
        if key not in self._memberships:
            raise TenantMembershipError(
                f"No membership for user {user_id} in tenant {tenant_id}"
            )
        self._memberships.pop(key, None)

    def update_membership_role(
        self, tenant_id: UUID, user_id: str, role: Role
    ) -> TenantMembership:
        """Change a membership's role and return the updated record."""
        membership = self.get_membership(tenant_id, user_id)
        updated = membership.model_copy(update={"role": role})
        self._memberships[(tenant_id, user_id)] = updated
        return updated

    def get_membership(self, tenant_id: UUID, user_id: str) -> TenantMembership:
        """Return the membership identified by `(tenant_id, user_id)`."""
        key = (tenant_id, user_id)
        membership = self._memberships.get(key)
        if membership is None:
            raise TenantMembershipError(
                f"No membership for user {user_id} in tenant {tenant_id}"
            )
        return membership

    def list_memberships_for_user(self, user_id: str) -> list[TenantMembership]:
        """Return every membership for a given principal."""
        return [m for m in self._memberships.values() if m.user_id == user_id]

    def list_memberships_for_tenant(self, tenant_id: UUID) -> list[TenantMembership]:
        """Return every membership inside a tenant."""
        return [m for m in self._memberships.values() if m.tenant_id == tenant_id]

    def record_audit_event(self, event: TenantAuditEvent) -> TenantAuditEvent:
        """Persist a tenant audit event."""
        self._audit_events.append(event)
        return event

    def list_audit_events(
        self, tenant_id: UUID, *, limit: int = 100
    ) -> list[TenantAuditEvent]:
        """Return the most recent tenant audit events."""
        events = [event for event in self._audit_events if event.tenant_id == tenant_id]
        return list(reversed(events[-limit:]))
