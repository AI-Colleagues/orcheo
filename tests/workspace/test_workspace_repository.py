"""Tests for the workspace repositories (in-memory and SQLite)."""

from __future__ import annotations
from collections.abc import Iterator
from pathlib import Path
import pytest
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    Role,
    SqliteWorkspaceRepository,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceMembership,
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspaceSlugConflictError,
    WorkspaceStatus,
)


@pytest.fixture(params=["in_memory", "sqlite"])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[object]:
    if request.param == "in_memory":
        yield InMemoryWorkspaceRepository()
    else:
        yield SqliteWorkspaceRepository(tmp_path / "workspaces.sqlite")


def _make_workspace(slug: str = "acme", name: str = "Acme Inc") -> Workspace:
    return Workspace(slug=slug, name=name)


def test_create_and_lookup_workspace(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    repo.create_workspace(workspace)  # type: ignore[attr-defined]
    fetched = repo.get_workspace_by_slug("acme")  # type: ignore[attr-defined]
    assert fetched.id == workspace.id
    by_id = repo.get_workspace(workspace.id)  # type: ignore[attr-defined]
    assert by_id.slug == "acme"


def test_create_workspace_rejects_duplicate_slug(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    repo.create_workspace(_make_workspace())  # type: ignore[attr-defined]
    with pytest.raises(WorkspaceSlugConflictError):
        repo.create_workspace(_make_workspace(name="Acme 2"))  # type: ignore[attr-defined]


def test_get_workspace_by_slug_missing(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    with pytest.raises(WorkspaceNotFoundError):
        repo.get_workspace_by_slug("ghost")  # type: ignore[attr-defined]


def test_list_workspaces_filters_inactive(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    a = _make_workspace("aa", "A")
    b = _make_workspace("bb", "B")
    repo.create_workspace(a)  # type: ignore[attr-defined]
    repo.create_workspace(b)  # type: ignore[attr-defined]
    repo.update_status(b.id, WorkspaceStatus.SUSPENDED)  # type: ignore[attr-defined]
    actives = repo.list_workspaces()  # type: ignore[attr-defined]
    assert [t.slug for t in actives] == ["aa"]
    full = repo.list_workspaces(include_inactive=True)  # type: ignore[attr-defined]
    assert {t.slug for t in full} == {"aa", "bb"}


def test_membership_lifecycle(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    repo.create_workspace(workspace)  # type: ignore[attr-defined]
    repo.add_membership(  # type: ignore[attr-defined]
        WorkspaceMembership(workspace_id=workspace.id, user_id="alice", role=Role.OWNER)
    )
    repo.add_membership(  # type: ignore[attr-defined]
        WorkspaceMembership(workspace_id=workspace.id, user_id="bob", role=Role.EDITOR)
    )
    assert {m.user_id for m in repo.list_memberships_for_workspace(workspace.id)} == {  # type: ignore[attr-defined]
        "alice",
        "bob",
    }
    updated = repo.update_membership_role(workspace.id, "bob", Role.ADMIN)  # type: ignore[attr-defined]
    assert updated.role is Role.ADMIN
    repo.remove_membership(workspace.id, "bob")  # type: ignore[attr-defined]
    with pytest.raises(WorkspaceMembershipError):
        repo.remove_membership(workspace.id, "bob")  # type: ignore[attr-defined]


def test_delete_workspace_cascades_memberships(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    repo.create_workspace(workspace)  # type: ignore[attr-defined]
    repo.add_membership(  # type: ignore[attr-defined]
        WorkspaceMembership(workspace_id=workspace.id, user_id="alice", role=Role.OWNER)
    )
    repo.delete_workspace(workspace.id)  # type: ignore[attr-defined]
    with pytest.raises(WorkspaceNotFoundError):
        repo.get_workspace(workspace.id)  # type: ignore[attr-defined]
    assert repo.list_memberships_for_user("alice") == []  # type: ignore[attr-defined]


def test_soft_delete_sets_deleted_at(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    repo.create_workspace(workspace)  # type: ignore[attr-defined]
    updated = repo.update_status(workspace.id, WorkspaceStatus.DELETED)  # type: ignore[attr-defined]
    assert updated.deleted_at is not None
    assert updated.status is WorkspaceStatus.DELETED


def test_audit_events_round_trip(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    repo.create_workspace(workspace)  # type: ignore[attr-defined]
    event = WorkspaceAuditEvent(
        workspace_id=workspace.id,
        action="workspace.suspended",
        actor="admin",
        subject="alice",
        resource_type="workspace",
        resource_id=str(workspace.id),
        details={"reason": "maintenance"},
    )
    stored = repo.record_audit_event(event)  # type: ignore[attr-defined]
    assert stored.action == "workspace.suspended"
    events = repo.list_audit_events(workspace.id)  # type: ignore[attr-defined]
    assert events[-1].action == "workspace.suspended"


def test_add_membership_requires_existing_workspace(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    membership = WorkspaceMembership(
        workspace_id=workspace.id, user_id="alice", role=Role.OWNER
    )
    with pytest.raises(WorkspaceNotFoundError):
        repo.add_membership(membership)  # type: ignore[attr-defined]


def test_duplicate_membership_blocked(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    repo.create_workspace(workspace)  # type: ignore[attr-defined]
    repo.add_membership(  # type: ignore[attr-defined]
        WorkspaceMembership(workspace_id=workspace.id, user_id="alice", role=Role.OWNER)
    )
    with pytest.raises(WorkspaceMembershipError):
        repo.add_membership(  # type: ignore[attr-defined]
            WorkspaceMembership(
                workspace_id=workspace.id, user_id="alice", role=Role.EDITOR
            )
        )
