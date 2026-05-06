"""Workspace resolver: maps a principal to memberships and selects active workspace."""

from __future__ import annotations
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID
from orcheo.workspace.errors import (
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
)
from orcheo.workspace.models import (
    Role,
    Workspace,
    WorkspaceContext,
    WorkspaceMembership,
    WorkspaceStatus,
)
from orcheo.workspace.repository import WorkspaceRepository


__all__ = [
    "MembershipCache",
    "InMemoryMembershipCache",
    "WorkspaceResolver",
]


class MembershipCache(Protocol):
    """Cache protocol for workspace memberships."""

    def get(self, user_id: str) -> list[WorkspaceMembership] | None:
        """Return cached memberships for `user_id`, or None on miss."""

    def set(self, user_id: str, memberships: list[WorkspaceMembership]) -> None:
        """Store memberships for `user_id`."""

    def invalidate(self, user_id: str) -> None:
        """Drop the cached entry for `user_id`."""


@dataclass
class _CacheEntry:
    memberships: list[WorkspaceMembership]
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

    def get(self, user_id: str) -> list[WorkspaceMembership] | None:
        """Return cached memberships for `user_id`, or None when expired/missing."""
        entry = self._entries.get(user_id)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._entries.pop(user_id, None)
            return None
        return list(entry.memberships)

    def set(self, user_id: str, memberships: list[WorkspaceMembership]) -> None:
        """Store memberships for `user_id` with a freshly extended TTL."""
        self._entries[user_id] = _CacheEntry(
            memberships=list(memberships),
            expires_at=self._clock() + self._ttl,
        )

    def invalidate(self, user_id: str) -> None:
        """Drop any cached entry for `user_id`."""
        self._entries.pop(user_id, None)


class WorkspaceResolver:
    """Resolve workspaces for a given principal and workspace selector.

    The resolver caches memberships per `user_id` to avoid reading from the
    repository on every request. Membership mutations should call
    `invalidate(user_id)` to drop stale entries.
    """

    def __init__(
        self,
        repository: WorkspaceRepository,
        *,
        cache: MembershipCache | None = None,
        default_workspace_slug: str | None = None,
    ) -> None:
        """Bind the resolver to a repository and an optional cache override."""
        self._repository = repository
        self._cache = cache or InMemoryMembershipCache()
        self._default_workspace_slug = default_workspace_slug

    @property
    def repository(self) -> WorkspaceRepository:
        """Expose the underlying repository for advanced callers."""
        return self._repository

    def invalidate(self, user_id: str) -> None:
        """Drop cached memberships for the given user."""
        self._cache.invalidate(user_id)

    def list_memberships(self, user_id: str) -> list[WorkspaceMembership]:
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
        workspace_slug: str | None = None,
    ) -> WorkspaceContext:
        """Build a `WorkspaceContext` for `user_id`, optionally pinned to a slug.

        The slug is the workspace the caller asked for (e.g., header
        `X-Orcheo-Workspace`). When omitted, the resolver picks the principal's
        only membership. If the principal belongs to multiple workspaces, the
        configured default workspace is preferred when the user is a member of
        it; otherwise the caller must supply an explicit selector.
        """
        memberships = self.list_memberships(user_id)
        if not memberships:
            raise WorkspaceMembershipError(
                f"User {user_id} has no workspace memberships"
            )

        if workspace_slug is not None:
            try:
                workspace = self._repository.get_workspace_by_slug(workspace_slug)
            except WorkspaceNotFoundError:
                raise
            membership = self._select_membership(memberships, workspace.id)
            self._enforce_active(workspace)
            return self._make_context(workspace, membership)

        membership = self._select_primary_membership(memberships)
        workspace = self._repository.get_workspace(membership.workspace_id)
        self._enforce_active(workspace)
        return self._make_context(workspace, membership)

    @staticmethod
    def _enforce_active(workspace: Workspace) -> None:
        if workspace.status is not WorkspaceStatus.ACTIVE:
            raise WorkspacePermissionError(
                "Workspace "
                f"{workspace.slug} is not active "
                f"(status={workspace.status.value})"
            )

    @staticmethod
    def _select_membership(
        memberships: list[WorkspaceMembership], workspace_id: UUID
    ) -> WorkspaceMembership:
        for membership in memberships:
            if membership.workspace_id == workspace_id:
                return membership
        raise WorkspacePermissionError(
            f"User is not a member of workspace {workspace_id}"
        )

    def _select_primary_membership(
        self,
        memberships: list[WorkspaceMembership],
    ) -> WorkspaceMembership:
        """Return the principal's membership when no workspace is pinned."""
        if len(memberships) == 1:
            return memberships[0]

        if self._default_workspace_slug:
            try:
                workspace = self._repository.get_workspace_by_slug(
                    self._default_workspace_slug
                )
            except WorkspaceNotFoundError:
                workspace = None
            if workspace is not None:
                membership = self._select_membership(memberships, workspace.id)
                return membership

        raise WorkspacePermissionError(
            "Workspace selector is required when the user has multiple memberships"
        )

    @staticmethod
    def _make_context(
        workspace: Workspace, membership: WorkspaceMembership
    ) -> WorkspaceContext:
        return WorkspaceContext(
            workspace_id=workspace.id,
            workspace_slug=workspace.slug,
            user_id=membership.user_id,
            role=Role(membership.role),
            quotas=workspace.quotas,
        )
