"""Tenant resolver: maps a principal to memberships and selects active tenant."""

from __future__ import annotations
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID
from orcheo.tenancy.errors import (
    TenantMembershipError,
    TenantNotFoundError,
    TenantPermissionError,
)
from orcheo.tenancy.models import (
    Role,
    Tenant,
    TenantContext,
    TenantMembership,
    TenantStatus,
)
from orcheo.tenancy.repository import TenantRepository


__all__ = [
    "MembershipCache",
    "InMemoryMembershipCache",
    "TenantResolver",
]


class MembershipCache(Protocol):
    """Cache protocol for tenant memberships."""

    def get(self, user_id: str) -> list[TenantMembership] | None:
        """Return cached memberships for `user_id`, or None on miss."""

    def set(self, user_id: str, memberships: list[TenantMembership]) -> None:
        """Store memberships for `user_id`."""

    def invalidate(self, user_id: str) -> None:
        """Drop the cached entry for `user_id`."""


@dataclass
class _CacheEntry:
    memberships: list[TenantMembership]
    expires_at: float


class InMemoryMembershipCache:
    """Simple TTL-based in-memory cache used by default and for tests."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Configure the in-memory TTL and clock source."""
        self._ttl = ttl_seconds
        self._clock = clock or time.monotonic
        self._entries: dict[str, _CacheEntry] = {}

    def get(self, user_id: str) -> list[TenantMembership] | None:
        """Return cached memberships for `user_id`, or None when expired/missing."""
        entry = self._entries.get(user_id)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._entries.pop(user_id, None)
            return None
        return list(entry.memberships)

    def set(self, user_id: str, memberships: list[TenantMembership]) -> None:
        """Store memberships for `user_id` with a freshly extended TTL."""
        self._entries[user_id] = _CacheEntry(
            memberships=list(memberships),
            expires_at=self._clock() + self._ttl,
        )

    def invalidate(self, user_id: str) -> None:
        """Drop any cached entry for `user_id`."""
        self._entries.pop(user_id, None)


class TenantResolver:
    """Resolve tenants for a given principal and tenant selector.

    The resolver caches memberships per `user_id` to avoid reading from the
    repository on every request. Membership mutations should call
    `invalidate(user_id)` to drop stale entries.
    """

    def __init__(
        self,
        repository: TenantRepository,
        *,
        cache: MembershipCache | None = None,
    ) -> None:
        """Bind the resolver to a repository and an optional cache override."""
        self._repository = repository
        self._cache = cache or InMemoryMembershipCache()

    @property
    def repository(self) -> TenantRepository:
        """Expose the underlying repository for advanced callers."""
        return self._repository

    def invalidate(self, user_id: str) -> None:
        """Drop cached memberships for the given user."""
        self._cache.invalidate(user_id)

    def list_memberships(self, user_id: str) -> list[TenantMembership]:
        """Return memberships for `user_id`, using the cache when warm."""
        cached = self._cache.get(user_id)
        if cached is not None:
            return cached
        memberships = self._repository.list_memberships_for_user(user_id)
        self._cache.set(user_id, memberships)
        return list(memberships)

    def resolve(
        self,
        *,
        user_id: str,
        tenant_slug: str | None = None,
    ) -> TenantContext:
        """Build a `TenantContext` for `user_id`, optionally pinned to a slug.

        The slug is the tenant the caller asked for (e.g., header
        `X-Orcheo-Tenant`). When omitted, the resolver picks the principal's
        only membership; multiple memberships without a selector are an error.
        """
        memberships = self.list_memberships(user_id)
        if not memberships:
            raise TenantMembershipError(f"User {user_id} has no tenant memberships")

        if tenant_slug is not None:
            try:
                tenant = self._repository.get_tenant_by_slug(tenant_slug)
            except TenantNotFoundError:
                raise
            membership = self._select_membership(memberships, tenant.id)
            self._enforce_active(tenant)
            return self._make_context(tenant, membership)

        if len(memberships) > 1:
            raise TenantPermissionError(
                "Multiple memberships found; tenant must be specified explicitly"
            )

        membership = memberships[0]
        tenant = self._repository.get_tenant(membership.tenant_id)
        self._enforce_active(tenant)
        return self._make_context(tenant, membership)

    @staticmethod
    def _enforce_active(tenant: Tenant) -> None:
        if tenant.status is not TenantStatus.ACTIVE:
            raise TenantPermissionError(
                f"Tenant {tenant.slug} is not active (status={tenant.status.value})"
            )

    @staticmethod
    def _select_membership(
        memberships: list[TenantMembership], tenant_id: UUID
    ) -> TenantMembership:
        for membership in memberships:
            if membership.tenant_id == tenant_id:
                return membership
        raise TenantPermissionError(f"User is not a member of tenant {tenant_id}")

    @staticmethod
    def _make_context(tenant: Tenant, membership: TenantMembership) -> TenantContext:
        return TenantContext(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            user_id=membership.user_id,
            role=Role(membership.role),
            quotas=tenant.quotas,
        )
