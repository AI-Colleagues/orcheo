"""High-level tenant management service used by API and CLI layers."""

from __future__ import annotations
from collections.abc import Iterable
from uuid import UUID
from orcheo.tenancy.errors import TenantPermissionError
from orcheo.tenancy.models import (
    DEFAULT_TENANT_SLUG,
    Role,
    Tenant,
    TenantMembership,
    TenantQuotas,
    TenantStatus,
    normalize_slug,
)
from orcheo.tenancy.repository import TenantRepository
from orcheo.tenancy.resolver import TenantResolver


__all__ = ["TenantService", "ensure_default_tenant"]


def ensure_default_tenant(
    repository: TenantRepository,
    *,
    slug: str = DEFAULT_TENANT_SLUG,
    name: str = "Default Tenant",
) -> Tenant:
    """Return or create the deployment-wide default tenant.

    The default tenant is the home for all data in single-tenant deployments
    and for backfilled rows during the multi-tenancy migration.
    """
    try:
        return repository.get_tenant_by_slug(slug)
    except Exception:  # noqa: BLE001 - any "not found" surface is acceptable here
        tenant = Tenant(slug=slug, name=name)
        return repository.create_tenant(tenant)


class TenantService:
    """Coordinates tenant CRUD and membership operations.

    Responsibilities:
    - Validate slugs and roles.
    - Cascade cache invalidation on membership changes.
    - Centralize role-based authorization checks for sensitive actions.
    """

    def __init__(
        self,
        repository: TenantRepository,
        resolver: TenantResolver | None = None,
    ) -> None:
        """Bind the service to a repository and an optional resolver override."""
        self._repository = repository
        self._resolver = resolver or TenantResolver(repository)

    @property
    def repository(self) -> TenantRepository:
        """Expose the underlying repository."""
        return self._repository

    @property
    def resolver(self) -> TenantResolver:
        """Expose the bound tenant resolver."""
        return self._resolver

    def create_tenant(
        self,
        *,
        slug: str,
        name: str,
        owner_user_id: str,
        quotas: TenantQuotas | None = None,
    ) -> tuple[Tenant, TenantMembership]:
        """Create a tenant and assign `owner_user_id` as the owner."""
        tenant = Tenant(
            slug=normalize_slug(slug),
            name=name,
            quotas=quotas or TenantQuotas(),
        )
        created = self._repository.create_tenant(tenant)
        membership = TenantMembership(
            tenant_id=created.id,
            user_id=owner_user_id,
            role=Role.OWNER,
        )
        self._repository.add_membership(membership)
        self._resolver.invalidate(owner_user_id)
        return created, membership

    def list_tenants(self, *, include_inactive: bool = False) -> list[Tenant]:
        """Return tenants visible to operator-level callers."""
        return self._repository.list_tenants(include_inactive=include_inactive)

    def deactivate_tenant(self, tenant_id: UUID) -> Tenant:
        """Mark a tenant as suspended; runs and APIs reject requests."""
        return self._repository.update_status(tenant_id, TenantStatus.SUSPENDED)

    def reactivate_tenant(self, tenant_id: UUID) -> Tenant:
        """Move a suspended tenant back to active."""
        return self._repository.update_status(tenant_id, TenantStatus.ACTIVE)

    def soft_delete_tenant(self, tenant_id: UUID) -> Tenant:
        """Mark a tenant as deleted while preserving the row."""
        return self._repository.update_status(tenant_id, TenantStatus.DELETED)

    def hard_delete_tenant(self, tenant_id: UUID) -> None:
        """Remove a tenant and its memberships entirely."""
        memberships = self._repository.list_memberships_for_tenant(tenant_id)
        self._repository.delete_tenant(tenant_id)
        for membership in memberships:
            self._resolver.invalidate(membership.user_id)

    def invite_member(
        self,
        *,
        tenant_id: UUID,
        user_id: str,
        role: Role,
        actor_role: Role | None = None,
    ) -> TenantMembership:
        """Add a membership; if `actor_role` is given, enforces admin+ access."""
        if actor_role is not None and not actor_role.includes(Role.ADMIN):
            raise TenantPermissionError("Only admins or owners can invite new members")
        membership = TenantMembership(
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
        )
        added = self._repository.add_membership(membership)
        self._resolver.invalidate(user_id)
        return added

    def remove_member(
        self,
        *,
        tenant_id: UUID,
        user_id: str,
        actor_role: Role | None = None,
    ) -> None:
        """Remove a membership; admin+ when `actor_role` is provided."""
        if actor_role is not None and not actor_role.includes(Role.ADMIN):
            raise TenantPermissionError("Only admins or owners can remove members")
        self._repository.remove_membership(tenant_id, user_id)
        self._resolver.invalidate(user_id)

    def update_member_role(
        self,
        *,
        tenant_id: UUID,
        user_id: str,
        role: Role,
        actor_role: Role | None = None,
    ) -> TenantMembership:
        """Change a member's role inside a tenant."""
        if actor_role is not None and not actor_role.includes(Role.ADMIN):
            raise TenantPermissionError("Only admins or owners can change member roles")
        updated = self._repository.update_membership_role(tenant_id, user_id, role)
        self._resolver.invalidate(user_id)
        return updated

    def list_members(self, tenant_id: UUID) -> list[TenantMembership]:
        """Return memberships for a tenant."""
        return self._repository.list_memberships_for_tenant(tenant_id)

    def memberships_for(
        self, user_id: str, *, tenants: Iterable[Tenant] | None = None
    ) -> list[tuple[Tenant, TenantMembership]]:
        """Return paired tenant/membership records for a user."""
        memberships = self._resolver.list_memberships(user_id)
        tenant_lookup: dict[UUID, Tenant] = (
            {tenant.id: tenant for tenant in tenants} if tenants is not None else {}
        )
        result: list[tuple[Tenant, TenantMembership]] = []
        for membership in memberships:
            tenant = tenant_lookup.get(membership.tenant_id)
            if tenant is None:
                tenant = self._repository.get_tenant(membership.tenant_id)
            result.append((tenant, membership))
        return result
