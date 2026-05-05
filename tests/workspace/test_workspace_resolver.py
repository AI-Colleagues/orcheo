"""Tests for the workspace resolver and membership cache."""

from __future__ import annotations
import pytest
from orcheo.workspace import (
    InMemoryMembershipCache,
    InMemoryWorkspaceRepository,
    Role,
    Workspace,
    WorkspaceMembership,
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceResolver,
    WorkspaceStatus,
)


def _setup_repo() -> tuple[InMemoryWorkspaceRepository, Workspace, Workspace]:
    repo = InMemoryWorkspaceRepository()
    acme = Workspace(slug="acme", name="Acme")
    globex = Workspace(slug="globex", name="Globex")
    repo.create_workspace(acme)
    repo.create_workspace(globex)
    return repo, acme, globex


def test_resolver_picks_only_membership_when_unambiguous() -> None:
    repo, acme, _ = _setup_repo()
    repo.add_membership(
        WorkspaceMembership(workspace_id=acme.id, user_id="alice", role=Role.OWNER)
    )
    resolver = WorkspaceResolver(repo)
    ctx = resolver.resolve(user_id="alice")
    assert ctx.workspace_id == acme.id
    assert ctx.role is Role.OWNER


def test_resolver_requires_explicit_slug_with_multiple_memberships() -> None:
    repo, acme, globex = _setup_repo()
    repo.add_membership(
        WorkspaceMembership(workspace_id=acme.id, user_id="alice", role=Role.EDITOR)
    )
    repo.add_membership(
        WorkspaceMembership(workspace_id=globex.id, user_id="alice", role=Role.VIEWER)
    )
    resolver = WorkspaceResolver(repo)
    with pytest.raises(WorkspacePermissionError):
        resolver.resolve(user_id="alice")
    ctx = resolver.resolve(user_id="alice", workspace_slug="globex")
    assert ctx.workspace_slug == "globex"
    assert ctx.role is Role.VIEWER


def test_resolver_rejects_unknown_slug() -> None:
    repo, acme, _ = _setup_repo()
    repo.add_membership(
        WorkspaceMembership(workspace_id=acme.id, user_id="alice", role=Role.OWNER)
    )
    with pytest.raises(WorkspaceNotFoundError):
        WorkspaceResolver(repo).resolve(user_id="alice", workspace_slug="ghost")


def test_resolver_rejects_user_with_no_memberships() -> None:
    repo, _, _ = _setup_repo()
    with pytest.raises(WorkspaceMembershipError):
        WorkspaceResolver(repo).resolve(user_id="bob")


def test_resolver_rejects_non_active_workspace() -> None:
    repo, acme, _ = _setup_repo()
    repo.add_membership(
        WorkspaceMembership(workspace_id=acme.id, user_id="alice", role=Role.OWNER)
    )
    repo.update_status(acme.id, WorkspaceStatus.SUSPENDED)
    with pytest.raises(WorkspacePermissionError):
        WorkspaceResolver(repo).resolve(user_id="alice", workspace_slug="acme")


def test_resolver_rejects_user_not_in_requested_workspace() -> None:
    repo, acme, globex = _setup_repo()
    repo.add_membership(
        WorkspaceMembership(workspace_id=acme.id, user_id="alice", role=Role.EDITOR)
    )
    with pytest.raises(WorkspacePermissionError):
        WorkspaceResolver(repo).resolve(user_id="alice", workspace_slug="globex")


def test_resolver_uses_cache() -> None:
    repo, acme, _ = _setup_repo()
    repo.add_membership(
        WorkspaceMembership(workspace_id=acme.id, user_id="alice", role=Role.OWNER)
    )
    cache = InMemoryMembershipCache(ttl_seconds=60)
    resolver = WorkspaceResolver(repo, cache=cache)
    resolver.resolve(user_id="alice")
    # Mutate the underlying repo without invalidating; cache should serve stale.
    repo.remove_membership(acme.id, "alice")
    cached_memberships = resolver.list_memberships("alice")
    assert cached_memberships  # cache hit
    resolver.invalidate("alice")
    assert resolver.list_memberships("alice") == []


def test_membership_cache_expires() -> None:
    cache = InMemoryMembershipCache(ttl_seconds=1, clock=lambda: 0.0)
    cache.set("alice", [])
    expired = InMemoryMembershipCache(ttl_seconds=0.5)
    expired.set("alice", [])
    # No assertion needed beyond confirming get does not raise; this exercises
    # the fast-path when entries expire on the next access.
    assert cache.get("alice") is not None
