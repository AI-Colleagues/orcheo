"""PostgreSQL-backed implementation of the workspace repository."""

from __future__ import annotations
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID
from psycopg import Connection, connect
from psycopg.rows import dict_row
from orcheo.workspace.errors import (
    WorkspaceInvitationError,
    WorkspaceInvitationNotFoundError,
    WorkspaceMembershipError,
    WorkspaceNotFoundError,
    WorkspaceSlugConflictError,
)
from orcheo.workspace.models import (
    InvitationStatus,
    Role,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceQuotas,
    WorkspaceStatus,
    normalize_email,
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
            # Execute as one PostgreSQL script so dollar-quoted functions and
            # transaction blocks in additive feature schemas remain intact.
            conn.execute(POSTGRES_WORKSPACE_SCHEMA)

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
                    id, workspace_id, user_id, email, user_name, role, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(membership.id),
                    str(membership.workspace_id),
                    membership.user_id,
                    membership.email,
                    membership.user_name,
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

    def update_membership_identity(
        self,
        workspace_id: UUID,
        user_id: str,
        *,
        email: str | None = None,
        user_name: str | None = None,
    ) -> WorkspaceMembership:
        """Backfill a membership's identity fields; ``None`` values are ignored."""
        if email is None and user_name is None:
            return self.get_membership(workspace_id, user_id)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workspace_memberships
                   SET email = COALESCE(%s, email),
                       user_name = COALESCE(%s, user_name)
                 WHERE workspace_id = %s
                   AND user_id = %s
                """,
                (email, user_name, str(workspace_id), user_id),
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

    def list_memberships_for_email(self, email: str) -> list[WorkspaceMembership]:
        """Return every membership whose captured email matches (case-insensitive)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workspace_memberships WHERE LOWER(email) = LOWER(%s)",
                (email.strip(),),
            ).fetchall()
        return [self._row_to_membership(row) for row in rows]

    def reassign_membership(
        self, workspace_id: UUID, from_user_id: str, to_user_id: str
    ) -> WorkspaceMembership:
        """Re-key a membership's ``user_id`` from `from_user_id` to `to_user_id`."""
        if from_user_id == to_user_id:
            return self.get_membership(workspace_id, from_user_id)
        with self._connect() as conn:
            source = conn.execute(
                """
                SELECT 1 FROM workspace_memberships
                WHERE workspace_id = %s AND user_id = %s
                """,
                (str(workspace_id), from_user_id),
            ).fetchone()
            if source is None:
                raise WorkspaceMembershipError(
                    f"No membership for user {from_user_id} in workspace {workspace_id}"
                )
            collision = conn.execute(
                """
                SELECT 1 FROM workspace_memberships
                WHERE workspace_id = %s AND user_id = %s
                """,
                (str(workspace_id), to_user_id),
            ).fetchone()
            if collision is not None:
                raise WorkspaceMembershipError(
                    f"Membership already exists for user {to_user_id} in workspace "
                    f"{workspace_id}"
                )
            conn.execute(
                """
                UPDATE workspace_memberships
                   SET user_id = %s
                 WHERE workspace_id = %s AND user_id = %s
                """,
                (to_user_id, str(workspace_id), from_user_id),
            )
        return self.get_membership(workspace_id, to_user_id)

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

    def add_invitation(self, invitation: WorkspaceInvitation) -> WorkspaceInvitation:
        """Persist a new invitation; raises on a duplicate pending email."""
        with self._connect() as conn:
            workspace_row = conn.execute(
                "SELECT 1 FROM workspaces WHERE id = %s",
                (str(invitation.workspace_id),),
            ).fetchone()
            if workspace_row is None:
                raise WorkspaceNotFoundError(str(invitation.workspace_id))
            if invitation.status is InvitationStatus.PENDING:
                existing = conn.execute(
                    """
                    SELECT 1 FROM workspace_invitations
                    WHERE workspace_id = %s AND email = %s AND status = 'pending'
                    """,
                    (str(invitation.workspace_id), invitation.email),
                ).fetchone()
                if existing is not None:
                    raise WorkspaceInvitationError(
                        f"A pending invitation already exists for {invitation.email}"
                    )
            conn.execute(
                """
                INSERT INTO workspace_invitations (
                    id, workspace_id, email, role, token_hash, status,
                    invited_by, accepted_by, created_at, expires_at, accepted_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(invitation.id),
                    str(invitation.workspace_id),
                    invitation.email,
                    invitation.role.value,
                    invitation.token_hash,
                    invitation.status.value,
                    invitation.invited_by,
                    invitation.accepted_by,
                    invitation.created_at,
                    invitation.expires_at,
                    invitation.accepted_at,
                ),
            )
        return invitation

    def get_invitation(self, invitation_id: UUID) -> WorkspaceInvitation:
        """Return the invitation identified by `invitation_id`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_invitations WHERE id = %s",
                (str(invitation_id),),
            ).fetchone()
        if row is None:
            raise WorkspaceInvitationNotFoundError(str(invitation_id))
        return self._row_to_invitation(row)

    def get_invitation_by_token_hash(self, token_hash: str) -> WorkspaceInvitation:
        """Return the invitation matching a token hash."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_invitations WHERE token_hash = %s",
                (token_hash,),
            ).fetchone()
        if row is None:
            raise WorkspaceInvitationNotFoundError(token_hash)
        return self._row_to_invitation(row)

    def find_pending_invitation(
        self, workspace_id: UUID, email: str
    ) -> WorkspaceInvitation | None:
        """Return the pending invitation for `(workspace_id, email)` if any."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM workspace_invitations
                WHERE workspace_id = %s AND email = %s AND status = 'pending'
                """,
                (str(workspace_id), normalize_email(email)),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_invitation(row)

    def list_invitations(
        self, workspace_id: UUID, *, include_inactive: bool = True
    ) -> list[WorkspaceInvitation]:
        """Return invitations for a workspace, newest first."""
        query = "SELECT * FROM workspace_invitations WHERE workspace_id = %s"
        if not include_inactive:
            query += " AND status = 'pending'"
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, (str(workspace_id),)).fetchall()
        return [self._row_to_invitation(row) for row in rows]

    def update_invitation(self, invitation: WorkspaceInvitation) -> WorkspaceInvitation:
        """Persist status/acceptance changes for an existing invitation."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workspace_invitations
                   SET status = %s,
                       role = %s,
                       token_hash = %s,
                       accepted_by = %s,
                       expires_at = %s,
                       accepted_at = %s
                 WHERE id = %s
                """,
                (
                    invitation.status.value,
                    invitation.role.value,
                    invitation.token_hash,
                    invitation.accepted_by,
                    invitation.expires_at,
                    invitation.accepted_at,
                    str(invitation.id),
                ),
            )
            if cursor.rowcount == 0:
                raise WorkspaceInvitationNotFoundError(str(invitation.id))
        return invitation

    def accept_invitation_atomic(
        self,
        membership: WorkspaceMembership,
        invited_email: str,
        invitation: WorkspaceInvitation,
    ) -> tuple[WorkspaceMembership, WorkspaceInvitation]:
        """Atomically add/upsert membership and mark the invitation ACCEPTED."""
        now = _utc_now()
        with self._connect() as conn:
            existing_row = conn.execute(
                """
                SELECT * FROM workspace_memberships
                 WHERE workspace_id = %s AND user_id = %s
                """,
                (str(membership.workspace_id), membership.user_id),
            ).fetchone()
            if existing_row is None:
                conn.execute(
                    """
                    INSERT INTO workspace_memberships (
                        id, workspace_id, user_id, email, user_name, role, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(membership.id),
                        str(membership.workspace_id),
                        membership.user_id,
                        invited_email,
                        membership.user_name,
                        membership.role.value,
                        membership.created_at,
                    ),
                )
                final_membership = membership.model_copy(
                    update={"email": invited_email}
                )
            else:
                conn.execute(
                    """
                    UPDATE workspace_memberships
                       SET email = COALESCE(%s, email)
                     WHERE workspace_id = %s AND user_id = %s
                    """,
                    (invited_email, str(membership.workspace_id), membership.user_id),
                )
                row = conn.execute(
                    """
                    SELECT * FROM workspace_memberships
                     WHERE workspace_id = %s AND user_id = %s
                    """,
                    (str(membership.workspace_id), membership.user_id),
                ).fetchone()
                if row is None:
                    raise WorkspaceMembershipError(
                        f"No membership for user {membership.user_id} in workspace "
                        f"{membership.workspace_id}"
                    )
                final_membership = self._row_to_membership(row)

            accepted = invitation.model_copy(
                update={
                    "status": InvitationStatus.ACCEPTED,
                    "accepted_by": membership.user_id,
                    "accepted_at": now,
                }
            )
            cursor = conn.execute(
                """
                UPDATE workspace_invitations
                   SET status = %s,
                       accepted_by = %s,
                       accepted_at = %s
                 WHERE id = %s
                """,
                (
                    accepted.status.value,
                    accepted.accepted_by,
                    accepted.accepted_at,
                    str(accepted.id),
                ),
            )
            if cursor.rowcount == 0:
                raise WorkspaceInvitationNotFoundError(str(invitation.id))
        return final_membership, accepted

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
            quotas=WorkspaceQuotas(**cast(dict[str, Any], quotas_payload)),
            deleted_at=cast(datetime, deleted_at) if deleted_at else None,
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )

    @staticmethod
    def _row_to_membership(row: dict[str, object]) -> WorkspaceMembership:
        return WorkspaceMembership(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            user_id=str(row["user_id"]),
            email=None if row.get("email") is None else str(row["email"]),
            user_name=(None if row.get("user_name") is None else str(row["user_name"])),
            role=Role(str(row["role"])),
            created_at=cast(datetime, row["created_at"]),
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
            details=cast(dict[str, Any], details),
            created_at=cast(datetime, row["created_at"]),
        )

    @staticmethod
    def _row_to_invitation(row: dict[str, object]) -> WorkspaceInvitation:
        accepted_by = row.get("accepted_by")
        invited_by = row.get("invited_by")
        accepted_at = row.get("accepted_at")
        return WorkspaceInvitation(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            email=str(row["email"]),
            role=Role(str(row["role"])),
            token_hash=str(row["token_hash"]),
            status=InvitationStatus(str(row["status"])),
            invited_by=None if invited_by is None else str(invited_by),
            accepted_by=None if accepted_by is None else str(accepted_by),
            created_at=cast(datetime, row["created_at"]),
            expires_at=cast(datetime, row["expires_at"]),
            accepted_at=cast(datetime, accepted_at) if accepted_at else None,
        )
