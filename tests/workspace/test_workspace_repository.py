"""Tests for the in-memory workspace repository."""

from __future__ import annotations
from datetime import timedelta
from uuid import uuid4
import pytest
from orcheo.workspace import (
    InvitationStatus,
    InMemoryWorkspaceRepository,
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceInvitation,
    WorkspaceInvitationError,
    WorkspaceInvitationNotFoundError,
    WorkspaceMembership,
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspaceSlugConflictError,
    WorkspaceStatus,
)
from orcheo.models.base import _utcnow


@pytest.fixture
def repository() -> InMemoryWorkspaceRepository:
    return InMemoryWorkspaceRepository()


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


def test_update_membership_identity_backfills_and_preserves(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    repo.create_workspace(workspace)  # type: ignore[attr-defined]
    repo.add_membership(  # type: ignore[attr-defined]
        WorkspaceMembership(workspace_id=workspace.id, user_id="alice", role=Role.OWNER)
    )

    updated = repo.update_membership_identity(  # type: ignore[attr-defined]
        workspace.id, "alice", email="alice@example.com", user_name="Alice"
    )
    assert updated.email == "alice@example.com"
    assert updated.user_name == "Alice"

    # A None field must not clobber a previously stored value.
    preserved = repo.update_membership_identity(  # type: ignore[attr-defined]
        workspace.id, "alice", email="alice@new.com"
    )
    assert preserved.email == "alice@new.com"
    assert preserved.user_name == "Alice"
    # Role and other fields remain intact.
    assert preserved.role is Role.OWNER


def test_delete_workspace_cascades_memberships(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    other_workspace = _make_workspace("beta", "Beta Inc")
    repo.create_workspace(workspace)  # type: ignore[attr-defined]
    repo.create_workspace(other_workspace)  # type: ignore[attr-defined]
    repo.add_membership(  # type: ignore[attr-defined]
        WorkspaceMembership(workspace_id=workspace.id, user_id="alice", role=Role.OWNER)
    )
    repo.record_audit_event(  # type: ignore[attr-defined]
        WorkspaceAuditEvent(workspace_id=workspace.id, action="workspace.created")
    )
    repo.add_invitation(  # type: ignore[attr-defined]
        WorkspaceInvitation(
            workspace_id=other_workspace.id,
            email="beta@example.com",
            role=Role.EDITOR,
            token_hash="other-token",
            created_at=_utcnow(),
            expires_at=_utcnow() + timedelta(days=1),
        )
    )
    repo.add_invitation(  # type: ignore[attr-defined]
        WorkspaceInvitation(
            workspace_id=workspace.id,
            email="alice@example.com",
            role=Role.EDITOR,
            token_hash="token",
            created_at=_utcnow(),
            expires_at=_utcnow() + timedelta(days=1),
        )
    )
    repo.delete_workspace(workspace.id)  # type: ignore[attr-defined]
    with pytest.raises(WorkspaceNotFoundError):
        repo.get_workspace(workspace.id)  # type: ignore[attr-defined]
    assert repo.list_memberships_for_user("alice") == []  # type: ignore[attr-defined]
    assert repo.list_audit_events(workspace.id) == []  # type: ignore[attr-defined]
    assert repo.list_invitations(workspace.id) == []  # type: ignore[attr-defined]
    assert repo.list_invitations(other_workspace.id) != []  # type: ignore[attr-defined]


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


def test_get_workspace_missing_raises(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    with pytest.raises(WorkspaceNotFoundError):
        repo.get_workspace(uuid4())  # type: ignore[attr-defined]


def test_missing_membership_lookup_and_role_update_raise(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    repo.create_workspace(workspace)  # type: ignore[attr-defined]

    with pytest.raises(WorkspaceMembershipError):
        repo.get_membership(workspace.id, "ghost")  # type: ignore[attr-defined]

    with pytest.raises(WorkspaceMembershipError):
        repo.update_membership_role(  # type: ignore[attr-defined]
            workspace.id,
            "ghost",
            Role.ADMIN,
        )


def test_missing_workspace_update_and_delete_raise(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    missing_id = uuid4()

    with pytest.raises(WorkspaceNotFoundError):
        repo.update_status(missing_id, WorkspaceStatus.SUSPENDED)  # type: ignore[attr-defined]

    with pytest.raises(WorkspaceNotFoundError):
        repo.delete_workspace(missing_id)  # type: ignore[attr-defined]


def test_membership_identity_lookup_and_reassignment_paths(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    repo.create_workspace(workspace)  # type: ignore[attr-defined]
    alice = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id="alice",
        email="alice@example.com",
        role=Role.OWNER,
    )
    bob = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id="bob",
        email="bob@example.com",
        role=Role.EDITOR,
    )
    repo.add_membership(alice)  # type: ignore[attr-defined]
    repo.add_membership(bob)  # type: ignore[attr-defined]

    assert [
        m.user_id for m in repo.list_memberships_for_email(" ALICE@EXAMPLE.COM ")
    ] == [  # type: ignore[attr-defined]
        "alice"
    ]
    assert repo.update_membership_identity(workspace.id, "alice") == alice  # type: ignore[attr-defined]
    assert (
        repo.update_membership_identity(  # type: ignore[attr-defined]
            workspace.id, "alice", email="alice@new.com"
        ).email
        == "alice@new.com"
    )
    assert (
        repo.update_membership_identity(  # type: ignore[attr-defined]
            workspace.id, "alice", user_name="Alice"
        ).user_name
        == "Alice"
    )
    assert repo.reassign_membership(workspace.id, "alice", "alice").user_id == "alice"  # type: ignore[attr-defined]
    with pytest.raises(WorkspaceMembershipError):
        repo.reassign_membership(workspace.id, "ghost", "someone")  # type: ignore[attr-defined]
    repo.add_membership(  # type: ignore[attr-defined]
        WorkspaceMembership(
            workspace_id=workspace.id, user_id="carol", role=Role.VIEWER
        )
    )
    with pytest.raises(WorkspaceMembershipError):
        repo.reassign_membership(workspace.id, "bob", "carol")  # type: ignore[attr-defined]
    moved = repo.reassign_membership(workspace.id, "bob", "dave")  # type: ignore[attr-defined]
    assert moved.user_id == "dave"


def test_invitation_and_atomic_acceptance_paths(repository: object) -> None:
    repo = repository  # type: ignore[assignment]
    workspace = _make_workspace()
    repo.create_workspace(workspace)  # type: ignore[attr-defined]

    pending = WorkspaceInvitation(
        workspace_id=workspace.id,
        email="invitee@example.com",
        role=Role.EDITOR,
        token_hash="pending-token",
        created_at=_utcnow(),
        expires_at=_utcnow() + timedelta(days=1),
    )
    accepted = pending.model_copy(
        update={
            "id": uuid4(),
            "status": InvitationStatus.ACCEPTED,
            "token_hash": "accepted-token",
        }
    )

    repo.add_invitation(pending)  # type: ignore[attr-defined]
    repo.add_invitation(accepted)  # type: ignore[attr-defined]
    with pytest.raises(WorkspaceInvitationError):
        repo.add_invitation(  # type: ignore[attr-defined]
            pending.model_copy(update={"id": uuid4()})
        )
    with pytest.raises(WorkspaceNotFoundError):
        repo.add_invitation(  # type: ignore[attr-defined]
            WorkspaceInvitation(
                workspace_id=uuid4(),
                email="ghost@example.com",
                role=Role.VIEWER,
                token_hash="ghost",
                created_at=_utcnow(),
                expires_at=_utcnow() + timedelta(days=1),
            )
        )

    assert repo.get_invitation(pending.id) == pending  # type: ignore[attr-defined]
    assert repo.get_invitation_by_token_hash("pending-token") == pending  # type: ignore[attr-defined]
    assert repo.find_pending_invitation(workspace.id, "INVITEE@example.com") == pending  # type: ignore[attr-defined]
    assert repo.find_pending_invitation(workspace.id, "missing@example.com") is None  # type: ignore[attr-defined]
    assert [
        inv.id for inv in repo.list_invitations(workspace.id, include_inactive=False)
    ] == [pending.id]  # type: ignore[attr-defined]
    assert {
        inv.id for inv in repo.list_invitations(workspace.id, include_inactive=True)
    } == {pending.id, accepted.id}  # type: ignore[attr-defined]

    with pytest.raises(WorkspaceInvitationNotFoundError):
        repo.get_invitation(uuid4())  # type: ignore[attr-defined]
    with pytest.raises(WorkspaceInvitationNotFoundError):
        repo.get_invitation_by_token_hash("missing-token")  # type: ignore[attr-defined]
    with pytest.raises(WorkspaceInvitationNotFoundError):
        repo.update_invitation(  # type: ignore[attr-defined]
            pending.model_copy(update={"id": uuid4()})
        )

    updated = repo.update_invitation(  # type: ignore[attr-defined]
        pending.model_copy(update={"status": InvitationStatus.REVOKED})
    )
    assert updated.status is InvitationStatus.REVOKED

    inserted_membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id="new-user",
        email=None,
        role=Role.EDITOR,
    )
    inserted_invitation = WorkspaceInvitation(
        workspace_id=workspace.id,
        email="new-user@example.com",
        role=Role.EDITOR,
        token_hash="insert-token",
        created_at=_utcnow(),
        expires_at=_utcnow() + timedelta(days=1),
    )
    final_membership, accepted_invitation = repo.accept_invitation_atomic(  # type: ignore[attr-defined]
        inserted_membership,
        "new-user@example.com",
        inserted_invitation,
    )
    assert final_membership.email == "new-user@example.com"
    assert accepted_invitation.status is InvitationStatus.ACCEPTED
    existing_membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id="existing-user",
        email="old@example.com",
        role=Role.EDITOR,
    )
    repo.add_membership(existing_membership)  # type: ignore[attr-defined]
    updated_membership, updated_invitation = repo.accept_invitation_atomic(  # type: ignore[attr-defined]
        existing_membership,
        "existing-user@example.com",
        inserted_invitation.model_copy(
            update={"id": uuid4(), "token_hash": "update-token"}
        ),
    )
    assert updated_membership.email == "existing-user@example.com"
    assert updated_invitation.accepted_by == "existing-user"
