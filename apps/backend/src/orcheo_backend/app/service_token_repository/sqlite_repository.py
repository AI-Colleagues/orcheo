"""SQLite-backed implementation for service token persistence."""

from __future__ import annotations
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from orcheo_backend.app.authentication import ServiceTokenRecord
from orcheo_backend.app.service_token_repository.sqlite_schema import ensure_schema
from orcheo_backend.app.service_token_repository.sqlite_serialization import (
    row_to_record,
    serialize_datetime,
    serialize_string_set,
)
from .protocol import ServiceTokenRepository


class SqliteServiceTokenRepository(ServiceTokenRepository):
    """SQLite-backed implementation of ServiceTokenRepository."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the repository with the database path."""
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        ensure_schema(self._db_path)

    async def list_all(self) -> list[ServiceTokenRecord]:
        """Return all service token records."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM service_tokens ORDER BY created_at DESC"
            )
            rows = cursor.fetchall()
            return [row_to_record(row) for row in rows]

    async def list_active(
        self, *, now: datetime | None = None
    ) -> list[ServiceTokenRecord]:
        """Return all active service token records."""
        reference = (now or datetime.now(tz=UTC)).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM service_tokens
                WHERE revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC
                """,
                (reference,),
            )
            rows = cursor.fetchall()
            return [row_to_record(row) for row in rows]

    async def list_for_tenant(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> list[ServiceTokenRecord]:
        """Return active service token records owned by *tenant_id*."""
        reference = (now or datetime.now(tz=UTC)).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM service_tokens
                WHERE tenant_id = ?
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC
                """,
                (tenant_id, reference),
            )
            rows = cursor.fetchall()
            return [row_to_record(row) for row in rows]

    async def find_by_id(self, identifier: str) -> ServiceTokenRecord | None:
        """Look up a service token by identifier."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM service_tokens WHERE identifier = ?", (identifier,)
            )
            row = cursor.fetchone()
            return row_to_record(row) if row else None

    async def find_by_hash(self, secret_hash: str) -> ServiceTokenRecord | None:
        """Look up a service token by its SHA256 hash."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM service_tokens WHERE secret_hash = ?", (secret_hash,)
            )
            row = cursor.fetchone()
            return row_to_record(row) if row else None

    async def create(self, record: ServiceTokenRecord) -> ServiceTokenRecord:
        """Store a new service token record."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO service_tokens (
                    identifier, secret_hash, scopes, workspace_ids,
                    created_at, created_by, issued_at, expires_at,
                    rotation_expires_at, rotated_to, revoked_at,
                    revoked_by, revocation_reason, tenant_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.identifier,
                    record.secret_hash,
                    serialize_string_set(record.scopes),
                    serialize_string_set(record.workspace_ids),
                    datetime.now(tz=UTC).isoformat(),
                    None,
                    serialize_datetime(record.issued_at),
                    serialize_datetime(record.expires_at),
                    serialize_datetime(record.rotation_expires_at),
                    record.rotated_to,
                    serialize_datetime(record.revoked_at),
                    None,
                    record.revocation_reason,
                    record.tenant_id,
                ),
            )
            conn.commit()
        return record

    async def update(self, record: ServiceTokenRecord) -> ServiceTokenRecord:
        """Update an existing service token record."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE service_tokens
                SET secret_hash = ?,
                    scopes = ?,
                    workspace_ids = ?,
                    issued_at = ?,
                    expires_at = ?,
                    rotation_expires_at = ?,
                    rotated_to = ?,
                    revoked_at = ?,
                    revocation_reason = ?,
                    tenant_id = ?
                WHERE identifier = ?
                """,
                (
                    record.secret_hash,
                    serialize_string_set(record.scopes),
                    serialize_string_set(record.workspace_ids),
                    serialize_datetime(record.issued_at),
                    serialize_datetime(record.expires_at),
                    serialize_datetime(record.rotation_expires_at),
                    record.rotated_to,
                    serialize_datetime(record.revoked_at),
                    record.revocation_reason,
                    record.tenant_id,
                    record.identifier,
                ),
            )
            conn.commit()
        return record

    async def delete(self, identifier: str) -> None:
        """Remove a service token from storage."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM service_tokens WHERE identifier = ?", (identifier,)
            )
            conn.commit()

    async def record_usage(
        self,
        token_id: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Track token usage."""
        now = datetime.now(tz=UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT tenant_id FROM service_tokens WHERE identifier = ?",
                (token_id,),
            )
            row = cursor.fetchone()
            tenant_id = row[0] if row is not None else None
            conn.execute(
                """
                UPDATE service_tokens
                SET last_used_at = ?,
                    use_count = use_count + 1
                WHERE identifier = ?
                """,
                (now, token_id),
            )
            details: dict[str, Any] = {}
            if ip:
                details["ip"] = ip
            if user_agent:
                details["user_agent"] = user_agent
            conn.execute(
                """
                INSERT INTO service_token_audit_log
                    (
                        token_id,
                        action,
                        ip_address,
                        user_agent,
                        timestamp,
                        details,
                        tenant_id
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_id,
                    "used",
                    ip,
                    user_agent,
                    now,
                    json.dumps(details) if details else None,
                    tenant_id,
                ),
            )
            conn.commit()

    async def get_audit_log(
        self, token_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Retrieve audit log entries for a token."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM service_token_audit_log
                WHERE token_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (token_id, limit),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    async def record_audit_event(
        self,
        token_id: str,
        action: str,
        *,
        actor: str | None = None,
        ip: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record an audit event for a token."""
        now = datetime.now(tz=UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT tenant_id FROM service_tokens WHERE identifier = ?",
                (token_id,),
            )
            row = cursor.fetchone()
            tenant_id = row[0] if row is not None else None
            conn.execute(
                """
                INSERT INTO service_token_audit_log
                    (token_id, action, actor, ip_address, timestamp, details, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_id,
                    action,
                    actor,
                    ip,
                    now,
                    json.dumps(details) if details else None,
                    tenant_id,
                ),
            )
            conn.commit()


__all__ = ["SqliteServiceTokenRepository"]
