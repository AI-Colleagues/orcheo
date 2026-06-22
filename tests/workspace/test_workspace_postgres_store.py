"""Tests for the PostgreSQL-backed workspace repository."""

from __future__ import annotations
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
import pytest
from orcheo.workspace import (
    InvitationStatus,
    PostgresWorkspaceRepository,
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
from orcheo.workspace import postgres_store as pg_store


class FakeCursor:
    """Fake database cursor for testing."""

    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[Any] | None = None,
        rowcount: int = 1,
    ) -> None:
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeConnection:
    """Fake connection that records queries and returns canned responses."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.queries: list[tuple[str, Any | None]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        statement = query.strip()
        self.queries.append((statement, params))
        if statement.startswith("CREATE") or statement.startswith("ALTER"):
            return FakeCursor()
        response = self._responses.pop(0) if self._responses else {}
        if isinstance(response, FakeCursor):
            return response
        if isinstance(response, dict):
            return FakeCursor(
                row=response.get("row"),
                rows=response.get("rows"),
                rowcount=response.get("rowcount", 1),
            )
        if isinstance(response, list):
            return FakeCursor(rows=response)
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def fake_connect(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeConnection, str]:
    """Patch psycopg connect and return the fake connection plus DSN."""
    connection = FakeConnection([])
    monkeypatch.setattr(pg_store, "connect", lambda dsn, row_factory=None: connection)
    return connection, "postgresql://test"


def _workspace_row(workspace: Workspace) -> dict[str, Any]:
    return {
        "id": workspace.id,
        "slug": workspace.slug,
        "name": workspace.name,
        "status": workspace.status.value,
        "quotas": workspace.quotas.model_dump(),
        "deleted_at": workspace.deleted_at,
        "created_at": workspace.created_at,
        "updated_at": workspace.updated_at,
    }


def _membership_row(membership: WorkspaceMembership) -> dict[str, Any]:
    return {
        "id": membership.id,
        "workspace_id": membership.workspace_id,
        "user_id": membership.user_id,
        "role": membership.role.value,
        "created_at": membership.created_at,
    }


def _audit_row(event: WorkspaceAuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "workspace_id": event.workspace_id,
        "action": event.action,
        "actor": event.actor,
        "subject": event.subject,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "details": event.details,
        "created_at": event.created_at,
    }


def _db_workspace_row(workspace: Workspace) -> dict[str, Any]:
    row = _workspace_row(workspace)
    row["quotas"] = json.dumps(row["quotas"])
    row["created_at"] = row["created_at"].isoformat()
    row["updated_at"] = row["updated_at"].isoformat()
    if row["deleted_at"] is not None:
        row["deleted_at"] = row["deleted_at"].isoformat()
    return row


def _db_audit_row(event: WorkspaceAuditEvent) -> dict[str, Any]:
    row = _audit_row(event)
    row["details"] = json.dumps(row["details"])
    row["created_at"] = row["created_at"].isoformat()
    return row


def _invitation_row(invitation: WorkspaceInvitation) -> dict[str, Any]:
    return {
        "id": invitation.id,
        "workspace_id": invitation.workspace_id,
        "email": invitation.email,
        "role": invitation.role.value,
        "token_hash": invitation.token_hash,
        "status": invitation.status.value,
        "invited_by": invitation.invited_by,
        "accepted_by": invitation.accepted_by,
        "created_at": invitation.created_at,
        "expires_at": invitation.expires_at,
        "accepted_at": invitation.accepted_at,
    }


def test_postgres_workspace_repository_roundtrip(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    """Exercise the common CRUD and listing paths."""
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)

    workspace = Workspace(slug="acme", name="Acme")
    repo.create_workspace(workspace)

    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id="alice",
        role=Role.OWNER,
    )
    connection._responses.extend(
        [
            {"row": {"id": workspace.id}},  # add_membership workspace exists
            {"row": None},  # add_membership duplicate check
            {},  # add_membership insert
            {"row": _workspace_row(workspace)},  # get_workspace
            {"row": _workspace_row(workspace)},  # get_workspace_by_slug
            {"row": _membership_row(membership)},  # get_membership
            {"rows": [_membership_row(membership)]},  # list_memberships_for_user
            {"rows": [_membership_row(membership)]},  # list_memberships_for_workspace
            {},  # record_audit_event insert
            {
                "rows": [
                    _audit_row(
                        WorkspaceAuditEvent(
                            workspace_id=workspace.id, action="workspace.created"
                        )
                    )
                ]
            },
        ]
    )

    repo.add_membership(membership)
    assert repo.get_workspace(workspace.id).slug == "acme"
    assert repo.get_workspace_by_slug("ACME").id == workspace.id
    assert repo.get_membership(workspace.id, "alice").role is Role.OWNER
    assert repo.list_memberships_for_user("alice")[0].workspace_id == workspace.id
    assert repo.list_memberships_for_workspace(workspace.id)[0].user_id == "alice"
    event = WorkspaceAuditEvent(
        workspace_id=workspace.id,
        action="workspace.created",
        actor="alice",
        subject="alice",
    )
    repo.record_audit_event(event)
    assert repo.list_audit_events(workspace.id)[0].action == "workspace.created"
    assert connection.commits >= 2
    assert connection.closed >= 1


def test_postgres_workspace_repository_update_and_delete(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    """Exercise status updates, deletions, and not-found paths."""
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)

    workspace = Workspace(slug="globex", name="Globex")
    updated_workspace = Workspace(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        status=WorkspaceStatus.SUSPENDED,
        quotas=workspace.quotas,
        deleted_at=None,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )
    connection._responses.extend(
        [
            {"row": None},  # create_workspace slug check
            {},  # create_workspace insert
            {"rowcount": 1},  # update_status
            {"row": _workspace_row(updated_workspace)},  # get_workspace after update
            {"rowcount": 1},  # delete_workspace
            {"row": None},  # get_workspace for missing lookup
        ]
    )
    repo.create_workspace(workspace)
    updated = repo.update_status(workspace.id, WorkspaceStatus.SUSPENDED)
    assert updated.status is WorkspaceStatus.SUSPENDED
    repo.delete_workspace(workspace.id)

    with pytest.raises(WorkspaceNotFoundError):
        repo.get_workspace(uuid4())


def test_postgres_workspace_repository_add_invitation_missing_workspace(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)

    invitation = WorkspaceInvitation(
        workspace_id=uuid4(),
        email="invitee@example.com",
        role=Role.EDITOR,
        token_hash="missing-workspace",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    connection._responses.extend([{"row": None}])

    with pytest.raises(WorkspaceNotFoundError):
        repo.add_invitation(invitation)


def test_postgres_workspace_repository_raises_on_duplicate_slug(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    """Duplicate workspace slugs should raise a dedicated error."""
    connection, dsn = fake_connect
    connection._responses.extend([{"row": {"id": uuid4()}}])
    repo = PostgresWorkspaceRepository(dsn)

    workspace = Workspace(slug="acme", name="Acme")
    with pytest.raises(WorkspaceSlugConflictError, match="acme"):
        repo.create_workspace(workspace)


def test_postgres_workspace_repository_lists_and_parses_rows(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)
    connection.queries.clear()

    active = Workspace(slug="acme", name="Acme")
    inactive = Workspace(
        id=uuid4(),
        slug="globex",
        name="Globex",
        status=WorkspaceStatus.SUSPENDED,
        quotas=active.quotas,
        deleted_at=datetime.now(tz=UTC),
        created_at=active.created_at,
        updated_at=active.updated_at,
    )
    audit_event = WorkspaceAuditEvent(
        workspace_id=active.id,
        action="workspace.suspended",
        actor=None,
        subject=None,
        resource_type=None,
        resource_id=None,
        details={"reason": "maintenance"},
    )

    connection._responses.extend(
        [
            {"rows": [_db_workspace_row(active)]},
            {"rows": [_db_workspace_row(active), _db_workspace_row(inactive)]},
            {"rows": [_db_audit_row(audit_event)]},
        ]
    )

    active_only = repo.list_workspaces()
    all_workspaces = repo.list_workspaces(include_inactive=True)
    audit_events = repo.list_audit_events(active.id)

    assert [workspace.slug for workspace in active_only] == ["acme"]
    assert {workspace.slug for workspace in all_workspaces} == {"acme", "globex"}
    assert audit_events[0].details == {"reason": "maintenance"}
    assert "WHERE status = 'active'" in connection.queries[0][0]
    assert "WHERE status = 'active'" not in connection.queries[1][0]


def test_postgres_workspace_repository_missing_workspace_and_status_paths(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)
    connection.queries.clear()

    connection._responses.extend(
        [
            {"row": None},
            {"rowcount": 0},
            {"rowcount": 0},
        ]
    )

    with pytest.raises(WorkspaceNotFoundError):
        repo.get_workspace(uuid4())

    with pytest.raises(WorkspaceNotFoundError):
        repo.update_status(uuid4(), WorkspaceStatus.SUSPENDED)

    with pytest.raises(WorkspaceNotFoundError):
        repo.delete_workspace(uuid4())


def test_postgres_workspace_repository_add_membership_errors(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)
    connection.queries.clear()

    workspace = Workspace(slug="acme", name="Acme")
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id="alice",
        role=Role.OWNER,
    )
    connection._responses.extend(
        [
            {"row": None},
            {"row": {"id": str(workspace.id)}},
            {"row": {"id": str(uuid4())}},
        ]
    )

    with pytest.raises(WorkspaceNotFoundError):
        repo.add_membership(membership)

    with pytest.raises(WorkspaceMembershipError):
        repo.add_membership(membership)


def test_postgres_workspace_repository_membership_lookup_errors(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)
    connection.queries.clear()

    workspace_id = uuid4()
    connection._responses.extend(
        [
            {"rowcount": 0},
            {"rowcount": 0},
            {"row": None},
        ]
    )

    with pytest.raises(WorkspaceMembershipError):
        repo.remove_membership(workspace_id, "ghost")

    with pytest.raises(WorkspaceMembershipError):
        repo.update_membership_role(workspace_id, "ghost", Role.ADMIN)

    with pytest.raises(WorkspaceMembershipError):
        repo.get_membership(workspace_id, "ghost")


def test_postgres_workspace_repository_membership_updates(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)
    connection.queries.clear()

    workspace = Workspace(slug="acme", name="Acme")
    updated_membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id="alice",
        role=Role.ADMIN,
    )
    connection._responses.extend(
        [
            {"row": None},
            {"rowcount": 1},
            {"rowcount": 1},
            {"row": _membership_row(updated_membership)},
        ]
    )

    with pytest.raises(WorkspaceNotFoundError):
        repo.get_workspace_by_slug("ghost")

    repo.remove_membership(workspace.id, "alice")
    updated = repo.update_membership_role(workspace.id, "alice", Role.ADMIN)
    assert updated.role is Role.ADMIN


def test_postgres_workspace_repository_membership_identity_paths(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)

    workspace = Workspace(slug="acme", name="Acme")
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        user_id="alice",
        role=Role.OWNER,
    )
    connection._responses.extend(
        [
            {"row": None},
            {},
            {"row": {"id": str(workspace.id)}},
            {"row": None},
            {},
            {"row": _membership_row(membership)},
            {"rowcount": 1},
            {
                "row": {
                    **_membership_row(membership),
                    "email": "alice@example.com",
                }
            },
            {"rowcount": 1},
            {"row": {**_membership_row(membership), "user_name": "Alice"}},
            {"rowcount": 0},
        ]
    )

    repo.create_workspace(workspace)
    repo.add_membership(membership)

    assert repo.update_membership_identity(workspace.id, "alice") == membership
    assert (
        repo.update_membership_identity(
            workspace.id, "alice", email="alice@example.com"
        ).email
        == "alice@example.com"
    )
    assert (
        repo.update_membership_identity(
            workspace.id, "alice", user_name="Alice"
        ).user_name
        == "Alice"
    )
    with pytest.raises(WorkspaceMembershipError):
        repo.update_membership_identity(
            workspace.id, "alice", email="alice@missing.com"
        )


def test_postgres_workspace_repository_membership_lookup_and_reassign_paths(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)

    workspace_id = uuid4()
    source = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id="alice",
        role=Role.OWNER,
    )
    moved = source.model_copy(update={"user_id": "carol"})
    connection._responses.extend(
        [
            {"row": _membership_row(source)},
            {"rows": [_membership_row(source), _membership_row(moved)]},
            {"row": _membership_row(source)},
            {"row": None},
            {"row": _membership_row(source)},
            {"row": {"id": "collision"}},
            {"row": _membership_row(source)},
            {},
            {},
            {"row": _membership_row(moved)},
        ]
    )

    assert repo.get_membership(workspace_id, "alice") == source
    assert [
        member.user_id
        for member in repo.list_memberships_for_email("ALICE@example.com")
    ] == [  # type: ignore[attr-defined]
        "alice",
        "carol",
    ]
    assert repo.reassign_membership(workspace_id, "alice", "alice") == source
    with pytest.raises(WorkspaceMembershipError):
        repo.reassign_membership(workspace_id, "ghost", "bob")
    with pytest.raises(WorkspaceMembershipError):
        repo.reassign_membership(workspace_id, "alice", "bob")
    assert repo.reassign_membership(workspace_id, "alice", "carol") == moved


def test_postgres_workspace_repository_invitation_paths(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)

    workspace = Workspace(slug="acme", name="Acme")
    pending = WorkspaceInvitation(
        workspace_id=workspace.id,
        email="invitee@example.com",
        role=Role.EDITOR,
        token_hash="pending-token",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    accepted = pending.model_copy(
        update={
            "id": uuid4(),
            "status": InvitationStatus.ACCEPTED,
            "token_hash": "accepted-token",
            "accepted_by": "auth0|invitee",
            "accepted_at": datetime(2026, 1, 3, tzinfo=UTC),
        }
    )
    revoked = pending.model_copy(update={"status": InvitationStatus.REVOKED})

    connection._responses.extend(
        [
            {"row": None},
            {},
            {"row": {"id": str(workspace.id)}},
            {"row": None},
            {},
            {"row": {"id": str(workspace.id)}},
            {},
            {"row": {"id": str(workspace.id)}},
            {"row": {"id": str(workspace.id)}},
            {"row": _invitation_row(pending)},
            {"row": _invitation_row(pending)},
            {"row": _invitation_row(pending)},
            {"row": None},
            {"rows": [_invitation_row(pending)]},
            {"rows": [_invitation_row(pending), _invitation_row(accepted)]},
            {"row": None},
            {"row": None},
            {"rowcount": 0},
            {"rowcount": 1},
        ]
    )

    repo.create_workspace(workspace)
    assert repo.add_invitation(pending) == pending
    assert repo.add_invitation(accepted) == accepted
    with pytest.raises(WorkspaceInvitationError):
        repo.add_invitation(pending.model_copy(update={"id": uuid4()}))
    assert repo.get_invitation(pending.id) == pending
    assert repo.get_invitation_by_token_hash("pending-token") == pending
    assert repo.find_pending_invitation(workspace.id, "INVITEE@example.com") == pending
    assert repo.find_pending_invitation(workspace.id, "missing@example.com") is None
    assert [
        inv.id for inv in repo.list_invitations(workspace.id, include_inactive=False)
    ] == [pending.id]
    assert {
        inv.id for inv in repo.list_invitations(workspace.id, include_inactive=True)
    } == {
        pending.id,
        accepted.id,
    }
    with pytest.raises(WorkspaceInvitationNotFoundError):
        repo.get_invitation(uuid4())
    with pytest.raises(WorkspaceInvitationNotFoundError):
        repo.get_invitation_by_token_hash("missing-token")
    with pytest.raises(WorkspaceInvitationNotFoundError):
        repo.update_invitation(pending.model_copy(update={"id": uuid4()}))
    updated = repo.update_invitation(revoked)
    assert updated.status is InvitationStatus.REVOKED


def test_postgres_workspace_repository_accept_invitation_atomic_paths(
    fake_connect: tuple[FakeConnection, str],
) -> None:
    connection, dsn = fake_connect
    repo = PostgresWorkspaceRepository(dsn)

    workspace_id = uuid4()
    insert_membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id="alice",
        role=Role.EDITOR,
    )
    existing_membership = WorkspaceMembership(
        workspace_id=workspace_id,
        user_id="bob",
        role=Role.ADMIN,
    )
    insert_invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        email="alice@example.com",
        role=Role.EDITOR,
        token_hash="insert-token",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    update_invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        email="bob@example.com",
        role=Role.ADMIN,
        token_hash="update-token",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    failing_invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        email="carol@example.com",
        role=Role.VIEWER,
        token_hash="failing-token",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    updated_row = _membership_row(
        existing_membership.model_copy(update={"email": "bob@example.com"})
    )

    connection._responses.extend(
        [
            None,
            {},
            {"rowcount": 1},
            {"row": _membership_row(existing_membership)},
            {},
            {"row": updated_row},
            {"rowcount": 1},
            {"row": _membership_row(existing_membership)},
            {},
            {"row": updated_row},
            {"rowcount": 0},
        ]
    )

    inserted_membership, accepted_insert = repo.accept_invitation_atomic(
        insert_membership,
        "alice@example.com",
        insert_invitation,
    )
    assert inserted_membership.email == "alice@example.com"
    assert accepted_insert.status is InvitationStatus.ACCEPTED

    updated_membership, accepted_update = repo.accept_invitation_atomic(
        existing_membership,
        "bob@example.com",
        update_invitation,
    )
    assert updated_membership.user_id == "bob"
    assert accepted_update.accepted_by == "bob"

    with pytest.raises(WorkspaceInvitationNotFoundError):
        repo.accept_invitation_atomic(
            existing_membership,
            "carol@example.com",
            failing_invitation,
        )


def test_postgres_workspace_repository_invitation_row_mapper_handles_nulls() -> None:
    invitation = WorkspaceInvitation(
        workspace_id=uuid4(),
        email="invitee@example.com",
        role=Role.EDITOR,
        token_hash="token",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    row = _invitation_row(
        invitation.model_copy(
            update={
                "invited_by": "auth0|owner",
                "accepted_by": "auth0|invitee",
                "accepted_at": datetime(2026, 1, 3, tzinfo=UTC),
            }
        )
    )
    row_with_nulls = _invitation_row(invitation)

    mapped = PostgresWorkspaceRepository._row_to_invitation(row)
    null_mapped = PostgresWorkspaceRepository._row_to_invitation(row_with_nulls)

    assert mapped.invited_by == "auth0|owner"
    assert mapped.accepted_by == "auth0|invitee"
    assert mapped.accepted_at == datetime(2026, 1, 3, tzinfo=UTC)
    assert null_mapped.invited_by is None
    assert null_mapped.accepted_by is None
    assert null_mapped.accepted_at is None
