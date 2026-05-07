"""Tests for the PostgreSQL-backed workspace repository."""

from __future__ import annotations
from typing import Any
from uuid import uuid4
import pytest
from orcheo.workspace import (
    PostgresWorkspaceRepository,
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceMembership,
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
        if statement.startswith("CREATE TABLE") or statement.startswith("CREATE INDEX"):
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
