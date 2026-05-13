"""Tests for the high-level workspace service."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import pytest
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    Role,
    WorkspaceMembershipError,
    WorkspaceMembershipLimitError,
    WorkspacePermissionError,
    WorkspaceService,
    WorkspaceStatus,
    ensure_default_workspace,
)


def _service() -> WorkspaceService:
    return WorkspaceService(InMemoryWorkspaceRepository())


def test_create_workspace_assigns_owner_membership() -> None:
    svc = _service()
    workspace, membership = svc.create_workspace(
        slug="acme", name="Acme", owner_user_id="alice"
    )
    assert workspace.slug == "acme"
    assert membership.role is Role.OWNER
    memberships = svc.resolver.list_memberships("alice")
    assert [m.workspace_id for m in memberships] == [workspace.id]


def test_invite_member_and_role_check() -> None:
    svc = _service()
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    svc.invite_member(
        workspace_id=workspace.id,
        user_id="bob",
        role=Role.VIEWER,
        actor_role=Role.OWNER,
    )
    with pytest.raises(WorkspacePermissionError):
        svc.invite_member(
            workspace_id=workspace.id,
            user_id="charlie",
            role=Role.VIEWER,
            actor_role=Role.EDITOR,
        )


def test_remove_member_invalidates_cache() -> None:
    svc = _service()
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    svc.invite_member(workspace_id=workspace.id, user_id="bob", role=Role.EDITOR)
    assert svc.resolver.list_memberships("bob")
    svc.remove_member(workspace_id=workspace.id, user_id="bob")
    assert svc.resolver.list_memberships("bob") == []


def test_remove_member_requires_admin_actor() -> None:
    svc = _service()
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    svc.invite_member(workspace_id=workspace.id, user_id="bob", role=Role.EDITOR)
    with pytest.raises(WorkspacePermissionError):
        svc.remove_member(
            workspace_id=workspace.id,
            user_id="bob",
            actor_role=Role.EDITOR,
        )


def test_update_member_role_requires_admin() -> None:
    svc = _service()
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    svc.invite_member(workspace_id=workspace.id, user_id="bob", role=Role.EDITOR)
    updated = svc.update_member_role(
        workspace_id=workspace.id, user_id="bob", role=Role.ADMIN
    )
    assert updated.role is Role.ADMIN
    with pytest.raises(WorkspacePermissionError):
        svc.update_member_role(
            workspace_id=workspace.id,
            user_id="bob",
            role=Role.OWNER,
            actor_role=Role.EDITOR,
        )


def test_deactivate_and_hard_delete_workspace() -> None:
    svc = _service()
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    svc.deactivate_workspace(workspace.id)
    fetched = svc.repository.get_workspace(workspace.id)
    assert fetched.status is WorkspaceStatus.SUSPENDED
    svc.reactivate_workspace(workspace.id)
    assert svc.repository.get_workspace(workspace.id).status is WorkspaceStatus.ACTIVE
    soft_deleted = svc.soft_delete_workspace(workspace.id)
    assert soft_deleted.status is WorkspaceStatus.DELETED
    assert soft_deleted.deleted_at is not None
    purged = svc.purge_deleted_workspaces(retention_days=0)
    assert purged and purged[0].id == workspace.id
    assert svc.resolver.list_memberships("alice") == []


def test_ensure_default_workspace_idempotent() -> None:
    repo = InMemoryWorkspaceRepository()
    a = ensure_default_workspace(repo)
    b = ensure_default_workspace(repo)
    assert a.id == b.id
    assert a.slug == "default"


def test_remove_nonexistent_member_raises() -> None:
    svc = _service()
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    with pytest.raises(WorkspaceMembershipError):
        svc.remove_member(workspace_id=workspace.id, user_id="ghost")


def test_create_workspace_limits_memberships_per_user() -> None:
    svc = _service()
    svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    svc.create_workspace(slug="globex", name="Globex", owner_user_id="alice")
    svc.create_workspace(slug="initech", name="Initech", owner_user_id="alice")

    with pytest.raises(WorkspaceMembershipLimitError):
        svc.create_workspace(slug="umbrella", name="Umbrella", owner_user_id="alice")
    assert all(ws.slug != "umbrella" for ws in svc.list_workspaces())


def test_invite_member_limits_memberships_per_user() -> None:
    svc = _service()
    workspaces = [
        svc.create_workspace(slug=slug, name=slug.title(), owner_user_id="owner")[0]
        for slug in ("acme", "globex", "initech")
    ]
    svc.invite_member(
        workspace_id=workspaces[0].id,
        user_id="bob",
        role=Role.VIEWER,
    )
    svc.invite_member(
        workspace_id=workspaces[1].id,
        user_id="bob",
        role=Role.VIEWER,
    )
    svc.invite_member(
        workspace_id=workspaces[2].id,
        user_id="bob",
        role=Role.VIEWER,
    )

    extra = svc.create_workspace(slug="hooli", name="Hooli", owner_user_id="carol")[0]
    with pytest.raises(WorkspaceMembershipLimitError):
        svc.invite_member(
            workspace_id=extra.id,
            user_id="bob",
            role=Role.VIEWER,
        )


def test_list_members_and_memberships_for_use_workspace_lookup() -> None:
    svc = _service()
    acme, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    globex, _ = svc.create_workspace(
        slug="globex", name="Globex", owner_user_id="alice"
    )
    svc.invite_member(workspace_id=acme.id, user_id="bob", role=Role.VIEWER)
    svc.invite_member(workspace_id=globex.id, user_id="bob", role=Role.ADMIN)

    members = svc.list_members(acme.id)
    assert [member.user_id for member in members] == ["alice", "bob"]

    pairs = svc.memberships_for("bob", workspaces=[acme])
    assert [workspace.slug for workspace, _ in pairs] == ["acme", "globex"]
    assert [membership.role for _, membership in pairs] == [Role.VIEWER, Role.ADMIN]


def test_purge_deleted_workspaces_skips_recent_deletes() -> None:
    svc = _service()
    active, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    recent, _ = svc.create_workspace(slug="globex", name="Globex", owner_user_id="bob")
    old, _ = svc.create_workspace(slug="initech", name="Initech", owner_user_id="carol")

    recent_deleted = svc.soft_delete_workspace(recent.id)
    old_deleted = svc.soft_delete_workspace(old.id)
    recent_deleted.deleted_at = datetime.now(tz=UTC)
    old_deleted.deleted_at = datetime.now(tz=UTC) - timedelta(days=2)

    purged = svc.purge_deleted_workspaces(retention_days=1)
    assert [workspace.id for workspace in purged] == [old.id]
    assert svc.repository.get_workspace(active.id).status is WorkspaceStatus.ACTIVE
    assert svc.repository.get_workspace(recent.id).status is WorkspaceStatus.DELETED


def test_ensure_membership_capacity_allows_existing_workspace_membership() -> None:
    svc = _service()
    workspace, _ = svc.create_workspace(slug="acme", name="Acme", owner_user_id="alice")
    svc._ensure_membership_capacity("alice", workspace_id=workspace.id)
