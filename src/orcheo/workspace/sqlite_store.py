"""SQLite-backed implementation of the workspace repository."""

from __future__ import annotations
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
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


__all__ = [
    "SQLITE_WORKSPACE_SCHEMA_SQL",
    "SqliteWorkspaceRepository",
    "ensure_workspace_schema",
]


SQLITE_WORKSPACE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    quotas TEXT NOT NULL DEFAULT '{}',
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspaces_status ON workspaces(status);

CREATE TABLE IF NOT EXISTS workspace_memberships (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_memberships_workspace_user
    ON workspace_memberships(workspace_id, user_id);
CREATE INDEX IF NOT EXISTS idx_workspace_memberships_user
    ON workspace_memberships(user_id);

CREATE TABLE IF NOT EXISTS workspace_audit_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT,
    subject TEXT,
    resource_type TEXT,
    resource_id TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspace_audit_events_workspace
    ON workspace_audit_events(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_audit_events_created_at
    ON workspace_audit_events(created_at);
"""


def ensure_workspace_schema(db_path: str | Path) -> None:
    """Create workspace tables if missing."""
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SQLITE_WORKSPACE_SCHEMA_SQL)
        cursor = conn.execute("PRAGMA table_info(workspaces)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "deleted_at" not in existing_columns:
            conn.execute("ALTER TABLE workspaces ADD COLUMN deleted_at TEXT")


def _utc_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SqliteWorkspaceRepository:
    """Persistent workspace store backed by SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        """Open or create a SQLite database for workspace storage."""
        self._path = Path(db_path).expanduser()
        ensure_workspace_schema(self._path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def create_workspace(self, workspace: Workspace) -> Workspace:
        """Persist a new workspace; raises on slug conflict."""
        slug = normalize_slug(workspace.slug)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM workspaces WHERE slug = ?", (slug,)
            ).fetchone()
            if existing is not None:
                raise WorkspaceSlugConflictError(slug)
            conn.execute(
                """
                INSERT INTO workspaces (
                    id, slug, name, status, quotas, deleted_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(workspace.id),
                    slug,
                    workspace.name,
                    workspace.status.value,
                    json.dumps(workspace.quotas.model_dump()),
                    workspace.deleted_at.isoformat() if workspace.deleted_at else None,
                    workspace.created_at.isoformat(),
                    workspace.updated_at.isoformat(),
                ),
            )
        return workspace

    def get_workspace(self, workspace_id: UUID) -> Workspace:
        """Return the workspace identified by `workspace_id`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?", (str(workspace_id),)
            ).fetchone()
        if row is None:
            raise WorkspaceNotFoundError(str(workspace_id))
        return self._row_to_workspace(row)

    def get_workspace_by_slug(self, slug: str) -> Workspace:
        """Return the workspace identified by `slug`."""
        normalized = normalize_slug(slug)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE slug = ?", (normalized,)
            ).fetchone()
        if row is None:
            raise WorkspaceNotFoundError(normalized)
        return self._row_to_workspace(row)

    def list_workspaces(self, *, include_inactive: bool = False) -> list[Workspace]:
        """List workspaces, optionally including suspended/deleted ones."""
        with self._connect() as conn:
            if include_inactive:
                rows = conn.execute("SELECT * FROM workspaces ORDER BY slug").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workspaces WHERE status = 'active' ORDER BY slug"
                ).fetchall()
        return [self._row_to_workspace(row) for row in rows]

    def update_status(self, workspace_id: UUID, status: WorkspaceStatus) -> Workspace:
        """Mutate the workspace's status and return the updated record."""
        timestamp = _utc_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE workspaces SET status = ?, deleted_at = ?, updated_at = ? WHERE id = ?",  # noqa: E501
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
        """Hard-delete a workspace and cascade memberships via foreign keys."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM workspaces WHERE id = ?", (str(workspace_id),)
            )
            if cursor.rowcount == 0:
                raise WorkspaceNotFoundError(str(workspace_id))
            conn.execute(
                "DELETE FROM workspace_audit_events WHERE workspace_id = ?",
                (str(workspace_id),),
            )

    def add_membership(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        """Persist a new membership; raises on duplicates."""
        with self._connect() as conn:
            workspace_row = conn.execute(
                "SELECT 1 FROM workspaces WHERE id = ?",
                (str(membership.workspace_id),),
            ).fetchone()
            if workspace_row is None:
                raise WorkspaceNotFoundError(str(membership.workspace_id))
            existing = conn.execute(
                """
                SELECT 1 FROM workspace_memberships
                WHERE workspace_id = ? AND user_id = ?
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
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(membership.id),
                    str(membership.workspace_id),
                    membership.user_id,
                    membership.role.value,
                    membership.created_at.isoformat(),
                ),
            )
        return membership

    def remove_membership(self, workspace_id: UUID, user_id: str) -> None:
        """Remove a membership keyed by `(workspace_id, user_id)`."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM workspace_memberships
                WHERE workspace_id = ? AND user_id = ?
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
                UPDATE workspace_memberships SET role = ?
                WHERE workspace_id = ? AND user_id = ?
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
                WHERE workspace_id = ? AND user_id = ?
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
                "SELECT * FROM workspace_memberships WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [self._row_to_membership(row) for row in rows]

    def list_memberships_for_workspace(
        self, workspace_id: UUID
    ) -> list[WorkspaceMembership]:
        """Return every membership inside a workspace."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workspace_memberships WHERE workspace_id = ?",
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    event.created_at.isoformat(),
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
                WHERE workspace_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (str(workspace_id), limit),
            ).fetchall()
        return [self._row_to_audit_event(row) for row in rows]

    @staticmethod
    def _row_to_workspace(row: sqlite3.Row) -> Workspace:
        quotas_payload = json.loads(row["quotas"]) if row["quotas"] else {}
        return Workspace(
            id=UUID(row["id"]),
            slug=row["slug"],
            name=row["name"],
            status=WorkspaceStatus(row["status"]),
            quotas=WorkspaceQuotas(**quotas_payload),
            deleted_at=_to_dt(row["deleted_at"]) if row["deleted_at"] else None,
            created_at=_to_dt(row["created_at"]),
            updated_at=_to_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_membership(row: sqlite3.Row) -> WorkspaceMembership:
        return WorkspaceMembership(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            user_id=row["user_id"],
            role=Role(row["role"]),
            created_at=_to_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_audit_event(row: sqlite3.Row) -> WorkspaceAuditEvent:
        details = json.loads(row["details"]) if row["details"] else {}
        return WorkspaceAuditEvent(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            action=row["action"],
            actor=row["actor"],
            subject=row["subject"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            details=details,
            created_at=_to_dt(row["created_at"]),
        )
