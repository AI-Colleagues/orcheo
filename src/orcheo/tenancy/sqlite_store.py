"""SQLite-backed implementation of the tenant repository."""

from __future__ import annotations
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from orcheo.tenancy.errors import (
    TenantMembershipError,
    TenantNotFoundError,
    TenantSlugConflictError,
)
from orcheo.tenancy.models import (
    Role,
    Tenant,
    TenantAuditEvent,
    TenantMembership,
    TenantQuotas,
    TenantStatus,
    normalize_slug,
)


__all__ = ["SQLITE_TENANT_SCHEMA_SQL", "SqliteTenantRepository", "ensure_tenant_schema"]


SQLITE_TENANT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    quotas TEXT NOT NULL DEFAULT '{}',
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);

CREATE TABLE IF NOT EXISTS tenant_memberships (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_memberships_tenant_user
    ON tenant_memberships(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_user
    ON tenant_memberships(user_id);

CREATE TABLE IF NOT EXISTS tenant_audit_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT,
    subject TEXT,
    resource_type TEXT,
    resource_id TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tenant_audit_events_tenant
    ON tenant_audit_events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_audit_events_created_at
    ON tenant_audit_events(created_at);
"""


def ensure_tenant_schema(db_path: str | Path) -> None:
    """Create tenancy tables if missing."""
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SQLITE_TENANT_SCHEMA_SQL)
        cursor = conn.execute("PRAGMA table_info(tenants)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "deleted_at" not in existing_columns:
            conn.execute("ALTER TABLE tenants ADD COLUMN deleted_at TEXT")


def _utc_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SqliteTenantRepository:
    """Persistent tenant store backed by SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        """Open or create a SQLite database for tenant storage."""
        self._path = Path(db_path).expanduser()
        ensure_tenant_schema(self._path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def create_tenant(self, tenant: Tenant) -> Tenant:
        """Persist a new tenant; raises on slug conflict."""
        slug = normalize_slug(tenant.slug)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM tenants WHERE slug = ?", (slug,)
            ).fetchone()
            if existing is not None:
                raise TenantSlugConflictError(slug)
            conn.execute(
                """
                INSERT INTO tenants (
                    id, slug, name, status, quotas, deleted_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(tenant.id),
                    slug,
                    tenant.name,
                    tenant.status.value,
                    json.dumps(tenant.quotas.model_dump()),
                    tenant.deleted_at.isoformat() if tenant.deleted_at else None,
                    tenant.created_at.isoformat(),
                    tenant.updated_at.isoformat(),
                ),
            )
        return tenant

    def get_tenant(self, tenant_id: UUID) -> Tenant:
        """Return the tenant identified by `tenant_id`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tenants WHERE id = ?", (str(tenant_id),)
            ).fetchone()
        if row is None:
            raise TenantNotFoundError(str(tenant_id))
        return self._row_to_tenant(row)

    def get_tenant_by_slug(self, slug: str) -> Tenant:
        """Return the tenant identified by `slug`."""
        normalized = normalize_slug(slug)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tenants WHERE slug = ?", (normalized,)
            ).fetchone()
        if row is None:
            raise TenantNotFoundError(normalized)
        return self._row_to_tenant(row)

    def list_tenants(self, *, include_inactive: bool = False) -> list[Tenant]:
        """List tenants, optionally including suspended/deleted ones."""
        with self._connect() as conn:
            if include_inactive:
                rows = conn.execute("SELECT * FROM tenants ORDER BY slug").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tenants WHERE status = 'active' ORDER BY slug"
                ).fetchall()
        return [self._row_to_tenant(row) for row in rows]

    def update_status(self, tenant_id: UUID, status: TenantStatus) -> Tenant:
        """Mutate the tenant's status and return the updated record."""
        timestamp = _utc_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tenants SET status = ?, deleted_at = ?, updated_at = ? WHERE id = ?",  # noqa: E501
                (
                    status.value,
                    timestamp if status is TenantStatus.DELETED else None,
                    timestamp,
                    str(tenant_id),
                ),
            )
            if cursor.rowcount == 0:
                raise TenantNotFoundError(str(tenant_id))
        return self.get_tenant(tenant_id)

    def delete_tenant(self, tenant_id: UUID) -> None:
        """Hard-delete a tenant and cascade memberships via foreign keys."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM tenants WHERE id = ?", (str(tenant_id),))
            if cursor.rowcount == 0:
                raise TenantNotFoundError(str(tenant_id))
            conn.execute(
                "DELETE FROM tenant_audit_events WHERE tenant_id = ?",
                (str(tenant_id),),
            )

    def add_membership(self, membership: TenantMembership) -> TenantMembership:
        """Persist a new membership; raises on duplicates."""
        with self._connect() as conn:
            tenant_row = conn.execute(
                "SELECT 1 FROM tenants WHERE id = ?",
                (str(membership.tenant_id),),
            ).fetchone()
            if tenant_row is None:
                raise TenantNotFoundError(str(membership.tenant_id))
            existing = conn.execute(
                """
                SELECT 1 FROM tenant_memberships
                WHERE tenant_id = ? AND user_id = ?
                """,
                (str(membership.tenant_id), membership.user_id),
            ).fetchone()
            if existing is not None:
                raise TenantMembershipError(
                    f"Membership exists for {membership.user_id} in tenant "
                    f"{membership.tenant_id}"
                )
            conn.execute(
                """
                INSERT INTO tenant_memberships (
                    id, tenant_id, user_id, role, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(membership.id),
                    str(membership.tenant_id),
                    membership.user_id,
                    membership.role.value,
                    membership.created_at.isoformat(),
                ),
            )
        return membership

    def remove_membership(self, tenant_id: UUID, user_id: str) -> None:
        """Remove a membership keyed by `(tenant_id, user_id)`."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM tenant_memberships
                WHERE tenant_id = ? AND user_id = ?
                """,
                (str(tenant_id), user_id),
            )
            if cursor.rowcount == 0:
                raise TenantMembershipError(
                    f"No membership for user {user_id} in tenant {tenant_id}"
                )

    def update_membership_role(
        self, tenant_id: UUID, user_id: str, role: Role
    ) -> TenantMembership:
        """Change a membership's role and return the updated record."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE tenant_memberships SET role = ?
                WHERE tenant_id = ? AND user_id = ?
                """,
                (role.value, str(tenant_id), user_id),
            )
            if cursor.rowcount == 0:
                raise TenantMembershipError(
                    f"No membership for user {user_id} in tenant {tenant_id}"
                )
        return self.get_membership(tenant_id, user_id)

    def get_membership(self, tenant_id: UUID, user_id: str) -> TenantMembership:
        """Return the membership identified by `(tenant_id, user_id)`."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM tenant_memberships
                WHERE tenant_id = ? AND user_id = ?
                """,
                (str(tenant_id), user_id),
            ).fetchone()
        if row is None:
            raise TenantMembershipError(
                f"No membership for user {user_id} in tenant {tenant_id}"
            )
        return self._row_to_membership(row)

    def list_memberships_for_user(self, user_id: str) -> list[TenantMembership]:
        """Return every membership for a given principal."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tenant_memberships WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [self._row_to_membership(row) for row in rows]

    def list_memberships_for_tenant(self, tenant_id: UUID) -> list[TenantMembership]:
        """Return every membership inside a tenant."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tenant_memberships WHERE tenant_id = ?",
                (str(tenant_id),),
            ).fetchall()
        return [self._row_to_membership(row) for row in rows]

    def record_audit_event(self, event: TenantAuditEvent) -> TenantAuditEvent:
        """Persist a tenant audit event."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tenant_audit_events (
                    id, tenant_id, action, actor, subject, resource_type,
                    resource_id, details, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    str(event.tenant_id),
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
        self, tenant_id: UUID, *, limit: int = 100
    ) -> list[TenantAuditEvent]:
        """Return the most recent tenant audit events."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tenant_audit_events
                WHERE tenant_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (str(tenant_id), limit),
            ).fetchall()
        return [self._row_to_audit_event(row) for row in rows]

    @staticmethod
    def _row_to_tenant(row: sqlite3.Row) -> Tenant:
        quotas_payload = json.loads(row["quotas"]) if row["quotas"] else {}
        return Tenant(
            id=UUID(row["id"]),
            slug=row["slug"],
            name=row["name"],
            status=TenantStatus(row["status"]),
            quotas=TenantQuotas(**quotas_payload),
            deleted_at=_to_dt(row["deleted_at"]) if row["deleted_at"] else None,
            created_at=_to_dt(row["created_at"]),
            updated_at=_to_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_membership(row: sqlite3.Row) -> TenantMembership:
        return TenantMembership(
            id=UUID(row["id"]),
            tenant_id=UUID(row["tenant_id"]),
            user_id=row["user_id"],
            role=Role(row["role"]),
            created_at=_to_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_audit_event(row: sqlite3.Row) -> TenantAuditEvent:
        details = json.loads(row["details"]) if row["details"] else {}
        return TenantAuditEvent(
            id=UUID(row["id"]),
            tenant_id=UUID(row["tenant_id"]),
            action=row["action"],
            actor=row["actor"],
            subject=row["subject"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            details=details,
            created_at=_to_dt(row["created_at"]),
        )
