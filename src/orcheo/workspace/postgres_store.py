"""PostgreSQL-backed implementation of the workspace repository."""

from __future__ import annotations
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from psycopg import Connection, connect
from psycopg.rows import dict_row
from orcheo.workspace.errors import (
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspaceSlugConflictError,
)
from orcheo.workspace.models import (
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceMembership,
    WorkspaceQuotas,
    WorkspaceStatus,
    normalize_slug,
)
from orcheo.workspace.postgres_schema import POSTGRES_WORKSPACE_SCHEMA


__all__ = ["PostgresWorkspaceRepository"]


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class PostgresWorkspaceRepository:
    """Persistent workspace store backed by PostgreSQL."""

    def __init__(self, dsn: str) -> None:
        """Open or create a PostgreSQL database for workspace storage."""
        self._dsn = dsn
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[Connection[Any]]:
        connection = connect(self._dsn, row_factory=dict_row)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            for statement in POSTGRES_WORKSPACE_SCHEMA.strip().split(";"):
                sql = statement.strip()
                if sql:
                    conn.execute(sql)

    def create_workspace(self, workspace: Workspace) -> Workspace:
        """Persist a new workspace; raises on slug conflict."""
        slug = normalize_slug(workspace.slug)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM workspaces WHERE slug = %s",
                (slug,),
            ).fetchone()
            if existing is not None:
                raise WorkspaceSlugConflictError(slug)
            conn.execute(
                """
                INSERT INTO workspaces (
                    id, slug, name, status, quotas, deleted_at, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(workspace.id),
                    slug,
                    workspace.name,
                    workspace.status.value,
                    json.dumps(workspace.quotas.model_dump()),
                    workspace.deleted_at,
                    workspace.created_at,
                    workspace.updated_at,
                ),
            )
        return workspace

    def get_workspace(self, workspace_id: UUID) -> Workspace:
        """Return the workspace identified by `workspace_id`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = %s",
                (str(workspace_id),),
            ).fetchone()
        if row is None:
            raise WorkspaceNotFoundError(str(workspace_id))
        return self._row_to_workspace(row)

    def get_workspace_by_slug(self, slug: str) -> Workspace:
        """Return the workspace identified by `slug`."""
        normalized = normalize_slug(slug)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE slug = %s",
                (normalized,),
            ).fetchone()
        if row is None:
            raise WorkspaceNotFoundError(normalized)
        return self._row_to_workspace(row)

    def list_workspaces(self, *, include_inactive: bool = False) -> list[Workspace]:
        """List workspaces, optionally including suspended/deleted ones."""
        query = "SELECT * FROM workspaces"
        if not include_inactive:
            query += " WHERE status = 'active'"
        query += " ORDER BY slug"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [self._row_to_workspace(row) for row in rows]

    def update_status(self, workspace_id: UUID, status: WorkspaceStatus) -> Workspace:
        """Mutate the workspace's status and return the updated record."""
        timestamp = _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workspaces
                   SET status = %s,
                       deleted_at = %s,
                       updated_at = %s
                 WHERE id = %s
                """,
                (
                    status.value,
                    timestamp if status is WorkspaceStatus.DELETED else None,
                    timestamp,
                    str(workspace_id),
                ),
            )
            if cursor.rowcount == 0:
                raise WorkspaceNotFoundError(str(workspace_id))
        return self.get_workspace(workspace_id)

    def delete_workspace(self, workspace_id: UUID) -> None:
        """Hard-delete a workspace and cascade memberships."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM workspaces WHERE id = %s",
                (str(workspace_id),),
            )
            if cursor.rowcount == 0:
                raise WorkspaceNotFoundError(str(workspace_id))

    def add_membership(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        """Persist a new membership; raises on duplicates."""
        with self._connect() as conn:
            workspace_row = conn.execute(
                "SELECT 1 FROM workspaces WHERE id = %s",
                (str(membership.workspace_id),),
            ).fetchone()
            if workspace_row is None:
                raise WorkspaceNotFoundError(str(membership.workspace_id))
            existing = conn.execute(
                """
                SELECT 1 FROM workspace_memberships
                WHERE workspace_id = %s AND user_id = %s
                """,
                (str(membership.workspace_id), membership.user_id),
            ).fetchone()
            if existing is not None:
                raise WorkspaceMembershipError(
                    f"Membership exists for {membership.user_id} in workspace "
                    f"{membership.workspace_id}"
                )
            conn.execute(
                """
                INSERT INTO workspace_memberships (
                    id, workspace_id, user_id, role, created_at
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    str(membership.id),
                    str(membership.workspace_id),
                    membership.user_id,
                    membership.role.value,
                    membership.created_at,
                ),
            )
        return membership

    def remove_membership(self, workspace_id: UUID, user_id: str) -> None:
        """Remove a membership keyed by `(workspace_id, user_id)`."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM workspace_memberships
                 WHERE workspace_id = %s
                   AND user_id = %s
                """,
                (str(workspace_id), user_id),
            )
            if cursor.rowcount == 0:
                raise WorkspaceMembershipError(
                    f"No membership for user {user_id} in workspace {workspace_id}"
                )

    def update_membership_role(
        self, workspace_id: UUID, user_id: str, role: Role
    ) -> WorkspaceMembership:
        """Change a membership's role and return the updated record."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workspace_memberships
                   SET role = %s
                 WHERE workspace_id = %s
                   AND user_id = %s
                """,
                (role.value, str(workspace_id), user_id),
            )
            if cursor.rowcount == 0:
                raise WorkspaceMembershipError(
                    f"No membership for user {user_id} in workspace {workspace_id}"
                )
        return self.get_membership(workspace_id, user_id)

    def get_membership(self, workspace_id: UUID, user_id: str) -> WorkspaceMembership:
        """Return the membership identified by `(workspace_id, user_id)`."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM workspace_memberships
                WHERE workspace_id = %s AND user_id = %s
                """,
                (str(workspace_id), user_id),
            ).fetchone()
        if row is None:
            raise WorkspaceMembershipError(
                f"No membership for user {user_id} in workspace {workspace_id}"
            )
        return self._row_to_membership(row)

    def list_memberships_for_user(self, user_id: str) -> list[WorkspaceMembership]:
        """Return every membership for a given principal."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workspace_memberships WHERE user_id = %s",
                (user_id,),
            ).fetchall()
        return [self._row_to_membership(row) for row in rows]

    def list_memberships_for_workspace(
        self, workspace_id: UUID
    ) -> list[WorkspaceMembership]:
        """Return every membership inside a workspace."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workspace_memberships WHERE workspace_id = %s",
                (str(workspace_id),),
            ).fetchall()
        return [self._row_to_membership(row) for row in rows]

    def record_audit_event(self, event: WorkspaceAuditEvent) -> WorkspaceAuditEvent:
        """Persist a workspace audit event."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workspace_audit_events (
                    id, workspace_id, action, actor, subject, resource_type,
                    resource_id, details, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(event.id),
                    str(event.workspace_id),
                    event.action,
                    event.actor,
                    event.subject,
                    event.resource_type,
                    event.resource_id,
                    json.dumps(event.details or {}),
                    event.created_at,
                ),
            )
        return event

    def list_audit_events(
        self, workspace_id: UUID, *, limit: int = 100
    ) -> list[WorkspaceAuditEvent]:
        """Return the most recent workspace audit events."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM workspace_audit_events
                WHERE workspace_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (str(workspace_id), limit),
            ).fetchall()
        return [self._row_to_audit_event(row) for row in rows]

    @staticmethod
    def _row_to_workspace(row: dict[str, object]) -> Workspace:
        quotas_payload = row["quotas"] or {}
        if isinstance(quotas_payload, str):
            quotas_payload = json.loads(quotas_payload)
        deleted_at = row.get("deleted_at")
        return Workspace(
            id=UUID(str(row["id"])),
            slug=str(row["slug"]),
            name=str(row["name"]),
            status=WorkspaceStatus(str(row["status"])),
            quotas=WorkspaceQuotas(**dict(quotas_payload)),
            deleted_at=deleted_at,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_membership(row: dict[str, object]) -> WorkspaceMembership:
        return WorkspaceMembership(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            user_id=str(row["user_id"]),
            role=Role(str(row["role"])),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_audit_event(row: dict[str, object]) -> WorkspaceAuditEvent:
        details = row["details"] or {}
        if isinstance(details, str):
            details = json.loads(details)
        return WorkspaceAuditEvent(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            action=str(row["action"]),
            actor=row["actor"] if row["actor"] is None else str(row["actor"]),
            subject=row["subject"] if row["subject"] is None else str(row["subject"]),
            resource_type=(
                None if row["resource_type"] is None else str(row["resource_type"])
            ),
            resource_id=(
                None if row["resource_id"] is None else str(row["resource_id"])
            ),
            details=dict(details),
            created_at=row["created_at"],
        )
