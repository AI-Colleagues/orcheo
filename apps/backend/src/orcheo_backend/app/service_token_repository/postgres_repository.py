"""PostgreSQL-backed implementation for service token persistence."""

from __future__ import annotations
import asyncio
import importlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from orcheo_backend.app.authentication import ServiceTokenRecord
from orcheo_backend.app.service_token_repository.protocol import ServiceTokenRepository
from orcheo_backend.app.service_token_repository.sqlite_serialization import (
    serialize_datetime,
    serialize_string_set,
)


# Optional psycopg dependencies
_AsyncConnectionPool: Any | None
_DictRowFactory: Any | None

try:  # pragma: no cover - optional dependency
    _AsyncConnectionPool = importlib.import_module("psycopg_pool").AsyncConnectionPool
    _DictRowFactory = importlib.import_module("psycopg.rows").dict_row
except Exception:  # pragma: no cover - fallback when dependency missing
    _AsyncConnectionPool = None
    _DictRowFactory = None


POSTGRES_SERVICE_TOKEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS service_tokens (
    identifier TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL,
    scopes JSONB,
    workspace_ids JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_by TEXT,
    issued_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    last_used_at TIMESTAMP WITH TIME ZONE,
    use_count INTEGER DEFAULT 0,
    rotation_expires_at TIMESTAMP WITH TIME ZONE,
    rotated_to TEXT,
    rotated_from TEXT,
    revoked_at TIMESTAMP WITH TIME ZONE,
    revoked_by TEXT,
    revocation_reason TEXT,
    allowed_ip_ranges JSONB,
    rate_limit_override INTEGER,
    tenant_id TEXT
);

ALTER TABLE service_tokens ADD COLUMN IF NOT EXISTS tenant_id TEXT;

CREATE INDEX IF NOT EXISTS idx_service_tokens_hash
    ON service_tokens(secret_hash);
CREATE INDEX IF NOT EXISTS idx_service_tokens_expires
    ON service_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_service_tokens_active
    ON service_tokens(revoked_at) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_service_tokens_tenant
    ON service_tokens(tenant_id);
CREATE INDEX IF NOT EXISTS idx_service_tokens_tenant_identifier
    ON service_tokens(tenant_id, identifier);
CREATE INDEX IF NOT EXISTS idx_service_tokens_tenant_hash
    ON service_tokens(tenant_id, secret_hash);

CREATE TABLE IF NOT EXISTS service_token_audit_log (
    id SERIAL PRIMARY KEY,
    token_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT,
    ip_address TEXT,
    user_agent TEXT,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    details JSONB,
    tenant_id TEXT
);

ALTER TABLE service_token_audit_log ADD COLUMN IF NOT EXISTS tenant_id TEXT;

CREATE INDEX IF NOT EXISTS idx_audit_log_token
    ON service_token_audit_log(token_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
    ON service_token_audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_log_tenant
    ON service_token_audit_log(tenant_id);
"""


def _row_to_record(row: dict[str, Any]) -> ServiceTokenRecord:
    """Convert a PostgreSQL row into a ServiceTokenRecord."""

    def parse_ts(val: Any) -> datetime | None:
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        return datetime.fromisoformat(val)

    def parse_set(val: Any) -> frozenset[str]:
        if val is None:
            return frozenset()
        if isinstance(val, str):
            return frozenset(json.loads(val))
        if isinstance(val, list):
            return frozenset(val)
        return frozenset(val)

    return ServiceTokenRecord(
        identifier=row["identifier"],
        secret_hash=row["secret_hash"],
        scopes=parse_set(row.get("scopes")),
        workspace_ids=parse_set(row.get("workspace_ids")),
        issued_at=parse_ts(row.get("issued_at")),
        expires_at=parse_ts(row.get("expires_at")),
        rotation_expires_at=parse_ts(row.get("rotation_expires_at")),
        rotated_to=row.get("rotated_to"),
        revoked_at=parse_ts(row.get("revoked_at")),
        revocation_reason=row.get("revocation_reason"),
        tenant_id=row.get("tenant_id"),
    )


class PostgresServiceTokenRepository(ServiceTokenRepository):
    """PostgreSQL-backed implementation of ServiceTokenRepository."""

    def __init__(
        self,
        dsn: str,
        *,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        pool_timeout: float = 30.0,
        pool_max_idle: float = 300.0,
    ) -> None:
        """Initialize the repository with the database DSN."""
        if _AsyncConnectionPool is None or _DictRowFactory is None:
            msg = "PostgreSQL backend requires psycopg[binary,pool] to be installed."
            raise RuntimeError(msg)

        self._dsn = dsn
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool_timeout = pool_timeout
        self._pool_max_idle = pool_max_idle
        self._pool: Any | None = None
        self._lock = asyncio.Lock()
        self._pool_lock = asyncio.Lock()
        self._schema_lock = asyncio.Lock()
        self._initialized = False

    async def _get_pool(self) -> Any:
        """Return the connection pool, creating it if necessary."""
        if self._pool is not None:
            return self._pool

        async with self._pool_lock:
            if self._pool is not None:
                return self._pool

            pool_class = _AsyncConnectionPool
            assert pool_class is not None  # mypy
            self._pool = pool_class(
                self._dsn,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
                timeout=self._pool_timeout,
                max_idle=self._pool_max_idle,
                open=False,
                kwargs={
                    "autocommit": False,
                    "prepare_threshold": 0,
                    "row_factory": _DictRowFactory,
                },
            )
            await self._pool.open()
            return self._pool

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        async with self._schema_lock:
            if self._initialized:
                return

            async with self._connection() as conn:
                for raw_stmt in POSTGRES_SERVICE_TOKEN_SCHEMA.strip().split(";"):
                    stmt = raw_stmt.strip()
                    if stmt:
                        await conn.execute(stmt)

            self._initialized = True

    async def list_all(self) -> list[ServiceTokenRecord]:
        """Return all service token records."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM service_tokens ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
            return [_row_to_record(row) for row in rows]

    async def list_active(
        self, *, now: datetime | None = None
    ) -> list[ServiceTokenRecord]:
        """Return all active service token records."""
        await self._ensure_initialized()
        reference = now or datetime.now(tz=UTC)
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM service_tokens
                WHERE revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > %s)
                ORDER BY created_at DESC
                """,
                (reference,),
            )
            rows = await cursor.fetchall()
            return [_row_to_record(row) for row in rows]

    async def list_for_tenant(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> list[ServiceTokenRecord]:
        """Return active service token records owned by *tenant_id*."""
        await self._ensure_initialized()
        reference = now or datetime.now(tz=UTC)
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM service_tokens
                WHERE tenant_id = %s
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > %s)
                ORDER BY created_at DESC
                """,
                (tenant_id, reference),
            )
            rows = await cursor.fetchall()
            return [_row_to_record(row) for row in rows]

    async def find_by_id(self, identifier: str) -> ServiceTokenRecord | None:
        """Look up a service token by identifier."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM service_tokens WHERE identifier = %s", (identifier,)
            )
            row = await cursor.fetchone()
            return _row_to_record(row) if row else None

    async def find_by_hash(self, secret_hash: str) -> ServiceTokenRecord | None:
        """Look up a service token by its SHA256 hash."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM service_tokens WHERE secret_hash = %s", (secret_hash,)
            )
            row = await cursor.fetchone()
            return _row_to_record(row) if row else None

    async def create(self, record: ServiceTokenRecord) -> ServiceTokenRecord:
        """Store a new service token record."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            await conn.execute(
                """
                INSERT INTO service_tokens (
                    identifier, secret_hash, scopes, workspace_ids,
                    created_at, created_by, issued_at, expires_at,
                    rotation_expires_at, rotated_to, revoked_at,
                    revoked_by, revocation_reason, tenant_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.identifier,
                    record.secret_hash,
                    serialize_string_set(record.scopes),
                    serialize_string_set(record.workspace_ids),
                    datetime.now(tz=UTC),
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
        return record

    async def update(self, record: ServiceTokenRecord) -> ServiceTokenRecord:
        """Update an existing service token record."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            await conn.execute(
                """
                UPDATE service_tokens
                SET secret_hash = %s,
                    scopes = %s,
                    workspace_ids = %s,
                    issued_at = %s,
                    expires_at = %s,
                    rotation_expires_at = %s,
                    rotated_to = %s,
                    revoked_at = %s,
                    revocation_reason = %s,
                    tenant_id = %s
                WHERE identifier = %s
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
        return record

    async def delete(self, identifier: str) -> None:
        """Remove a service token from storage."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            await conn.execute(
                "DELETE FROM service_tokens WHERE identifier = %s", (identifier,)
            )

    async def record_usage(
        self,
        token_id: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Track token usage."""
        await self._ensure_initialized()
        now = datetime.now(tz=UTC)
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT tenant_id FROM service_tokens WHERE identifier = %s",
                (token_id,),
            )
            row = await cursor.fetchone()
            tenant_id = row.get("tenant_id") if row is not None else None
            await conn.execute(
                """
                UPDATE service_tokens
                SET last_used_at = %s,
                    use_count = use_count + 1
                WHERE identifier = %s
                """,
                (now, token_id),
            )
            details: dict[str, Any] = {}
            if ip:
                details["ip"] = ip
            if user_agent:
                details["user_agent"] = user_agent
            await conn.execute(
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
                VALUES (%s, %s, %s, %s, %s, %s, %s)
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

    async def get_audit_log(
        self, token_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Retrieve audit log entries for a token."""
        await self._ensure_initialized()
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM service_token_audit_log
                WHERE token_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (token_id, limit),
            )
            rows = await cursor.fetchall()
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
        await self._ensure_initialized()
        now = datetime.now(tz=UTC)
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT tenant_id FROM service_tokens WHERE identifier = %s",
                (token_id,),
            )
            row = await cursor.fetchone()
            tenant_id = row.get("tenant_id") if row is not None else None
            await conn.execute(
                """
                INSERT INTO service_token_audit_log
                    (token_id, action, actor, ip_address, timestamp, details, tenant_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
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

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


__all__ = ["PostgresServiceTokenRepository"]
