"""Tests for the membership re-key primitives and the identity backfill."""

from __future__ import annotations
import pytest
from orcheo.identity import (
    InMemoryIdentityRepository,
    backfill_identities,
    report_migration_coverage,
)
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    Role,
    Workspace,
    WorkspaceMembership,
    WorkspaceMembershipError,
    WorkspaceResolver,
)


def _workspace(repo: InMemoryWorkspaceRepository, slug: str) -> Workspace:
    return repo.create_workspace(Workspace(slug=slug, name=slug.title()))


def _add(
    repo: InMemoryWorkspaceRepository,
    workspace_id,
    user_id: str,
    *,
    email: str | None,
    role: Role = Role.EDITOR,
) -> WorkspaceMembership:
    return repo.add_membership(
        WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user_id,
            email=email,
            role=role,
        )
    )


# -- repository primitives ------------------------------------------------


def test_list_memberships_for_email_is_case_insensitive() -> None:
    repo = InMemoryWorkspaceRepository()
    ws = _workspace(repo, "acme")
    _add(repo, ws.id, "auth0|a", email="Alice@Example.com")
    _add(repo, ws.id, "auth0|b", email="bob@example.com")

    matches = repo.list_memberships_for_email("alice@EXAMPLE.com")
    assert [m.user_id for m in matches] == ["auth0|a"]


def test_reassign_membership_rekeys_user_id() -> None:
    repo = InMemoryWorkspaceRepository()
    ws = _workspace(repo, "acme")
    _add(repo, ws.id, "auth0|a", email="alice@example.com", role=Role.ADMIN)

    updated = repo.reassign_membership(ws.id, "auth0|a", "uuid-a")
    assert updated.user_id == "uuid-a"
    assert updated.role is Role.ADMIN
    assert repo.get_membership(ws.id, "uuid-a").user_id == "uuid-a"
    with pytest.raises(WorkspaceMembershipError):
        repo.get_membership(ws.id, "auth0|a")


def test_reassign_membership_collision_raises() -> None:
    repo = InMemoryWorkspaceRepository()
    ws = _workspace(repo, "acme")
    _add(repo, ws.id, "auth0|a", email="alice@example.com")
    _add(repo, ws.id, "uuid-a", email="alice@example.com")
    with pytest.raises(WorkspaceMembershipError, match="already exists"):
        repo.reassign_membership(ws.id, "auth0|a", "uuid-a")


# -- backfill -------------------------------------------------------------


def test_backfill_creates_users_and_rekeys() -> None:
    workspace_repo = InMemoryWorkspaceRepository()
    identity_repo = InMemoryIdentityRepository()
    ws1 = _workspace(workspace_repo, "acme")
    ws2 = _workspace(workspace_repo, "globex")
    _add(workspace_repo, ws1.id, "auth0|alice", email="alice@example.com")
    _add(workspace_repo, ws1.id, "auth0|bob", email="bob@example.com")
    _add(workspace_repo, ws2.id, "google|alice", email="alice@example.com")

    report = backfill_identities(
        identity_repo=identity_repo, workspace_repo=workspace_repo
    )

    # Two distinct emails -> two users; three memberships re-keyed.
    assert report.users_created == 2
    assert report.memberships_rekeyed == 3
    assert report.memberships_already_internal == 0
    assert report.fully_covered is True

    alice = identity_repo.get_user_by_email("alice@example.com")
    assert alice is not None
    # Both of Alice's memberships now point at her internal id.
    assert workspace_repo.get_membership(ws1.id, str(alice.id)).user_id == str(alice.id)
    assert workspace_repo.get_membership(ws2.id, str(alice.id)).user_id == str(alice.id)


def test_backfill_is_idempotent() -> None:
    workspace_repo = InMemoryWorkspaceRepository()
    identity_repo = InMemoryIdentityRepository()
    ws = _workspace(workspace_repo, "acme")
    _add(workspace_repo, ws.id, "auth0|alice", email="alice@example.com")

    first = backfill_identities(
        identity_repo=identity_repo, workspace_repo=workspace_repo
    )
    second = backfill_identities(
        identity_repo=identity_repo, workspace_repo=workspace_repo
    )

    assert first.memberships_rekeyed == 1
    assert second.memberships_rekeyed == 0
    assert second.users_created == 0
    assert second.memberships_already_internal == 1
    assert second.fully_covered is True


def test_backfill_resolves_collision_highest_role_wins() -> None:
    workspace_repo = InMemoryWorkspaceRepository()
    identity_repo = InMemoryIdentityRepository()
    ws = _workspace(workspace_repo, "acme")
    # Two sub-keyed rows for the same email and workspace, different roles.
    _add(
        workspace_repo, ws.id, "auth0|alice", email="alice@example.com", role=Role.ADMIN
    )
    _add(
        workspace_repo,
        ws.id,
        "google|alice",
        email="alice@example.com",
        role=Role.VIEWER,
    )

    report = backfill_identities(
        identity_repo=identity_repo, workspace_repo=workspace_repo
    )

    assert report.collisions_resolved == 1
    alice = identity_repo.get_user_by_email("alice@example.com")
    assert alice is not None
    membership = workspace_repo.get_membership(ws.id, str(alice.id))
    # Highest role (admin) wins; only one row remains for the workspace.
    assert membership.role is Role.ADMIN
    assert len(workspace_repo.list_memberships_for_workspace(ws.id)) == 1


def test_backfill_skips_memberships_without_email() -> None:
    workspace_repo = InMemoryWorkspaceRepository()
    identity_repo = InMemoryIdentityRepository()
    ws = _workspace(workspace_repo, "acme")
    _add(workspace_repo, ws.id, "service-token", email=None)

    report = backfill_identities(
        identity_repo=identity_repo, workspace_repo=workspace_repo
    )
    assert report.memberships_without_email == 1
    assert report.users_created == 0
    assert report.fully_covered is False


def test_backfill_invalidates_resolver_cache() -> None:
    workspace_repo = InMemoryWorkspaceRepository()
    identity_repo = InMemoryIdentityRepository()
    resolver = WorkspaceResolver(workspace_repo)
    ws = _workspace(workspace_repo, "acme")
    _add(workspace_repo, ws.id, "auth0|alice", email="alice@example.com")

    # Warm the cache on the old sub key.
    assert len(resolver.list_memberships("auth0|alice")) == 1

    backfill_identities(
        identity_repo=identity_repo,
        workspace_repo=workspace_repo,
        resolver=resolver,
    )

    # Cache for the old sub key is invalidated -> now resolves to no memberships.
    assert resolver.list_memberships("auth0|alice") == []
    alice = identity_repo.get_user_by_email("alice@example.com")
    assert alice is not None
    assert len(resolver.list_memberships(str(alice.id))) == 1


def test_coverage_report_tracks_remaining() -> None:
    workspace_repo = InMemoryWorkspaceRepository()
    identity_repo = InMemoryIdentityRepository()
    ws = _workspace(workspace_repo, "acme")
    _add(workspace_repo, ws.id, "auth0|alice", email="alice@example.com")

    before = report_migration_coverage(
        identity_repo=identity_repo, workspace_repo=workspace_repo
    )
    assert before.remaining_to_migrate == 1
    assert before.fully_covered is False

    backfill_identities(identity_repo=identity_repo, workspace_repo=workspace_repo)

    after = report_migration_coverage(
        identity_repo=identity_repo, workspace_repo=workspace_repo
    )
    assert after.remaining_to_migrate == 0
    assert after.keyed_by_internal_id == 1
    assert after.fully_covered is True
