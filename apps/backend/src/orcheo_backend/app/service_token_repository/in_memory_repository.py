"""In-memory service token repository for tests."""

from __future__ import annotations
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from orcheo_backend.app.authentication import ServiceTokenRecord
from .protocol import ServiceTokenRepository


class InMemoryServiceTokenRepository(ServiceTokenRepository):
    """In-memory implementation for testing."""

    def __init__(self) -> None:
        """Initialize empty in-memory storage."""
        self._tokens: dict[str, ServiceTokenRecord] = {}
        self._audit_log: list[dict[str, Any]] = []

    async def list_all(self) -> list[ServiceTokenRecord]:
        """Return all service token records."""
        return list(self._tokens.values())

    async def list_active(
        self, *, now: datetime | None = None
    ) -> list[ServiceTokenRecord]:
        """Return all active service token records."""
        reference = now or datetime.now(tz=UTC)
        return [
            record
            for record in self._tokens.values()
            if not record.is_revoked() and not record.is_expired(now=reference)
        ]

    async def list_for_tenant(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> list[ServiceTokenRecord]:
        """Return active service token records owned by *tenant_id*."""
        active = await self.list_active(now=now)
        return [record for record in active if record.tenant_id == tenant_id]

    async def find_by_id(self, identifier: str) -> ServiceTokenRecord | None:
        """Look up a service token by identifier."""
        return self._tokens.get(identifier)

    async def find_by_hash(self, secret_hash: str) -> ServiceTokenRecord | None:
        """Look up a service token by its SHA256 hash."""
        for record in self._tokens.values():
            if record.secret_hash == secret_hash:
                return record
        return None

    async def create(self, record: ServiceTokenRecord) -> ServiceTokenRecord:
        """Store a new service token record."""
        self._tokens[record.identifier] = record
        return record

    async def update(self, record: ServiceTokenRecord) -> ServiceTokenRecord:
        """Update an existing service token record."""
        self._tokens[record.identifier] = record
        return record

    async def delete(self, identifier: str) -> None:
        """Remove a service token from storage."""
        self._tokens.pop(identifier, None)

    async def record_usage(
        self,
        token_id: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Track token usage."""
        now = datetime.now(tz=UTC)
        record = self._tokens.get(token_id)
        if record is not None:
            self._tokens[token_id] = replace(
                record,
                last_used_at=now,
                use_count=record.use_count + 1,
            )
        self._audit_log.append(
            {
                "token_id": token_id,
                "action": "used",
                "ip_address": ip,
                "user_agent": user_agent,
                "timestamp": now.isoformat(),
            }
        )

    async def get_audit_log(
        self, token_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Retrieve audit log entries for a token."""
        entries = [e for e in self._audit_log if e["token_id"] == token_id]
        return entries[-limit:]

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
        self._audit_log.append(
            {
                "token_id": token_id,
                "action": action,
                "actor": actor,
                "ip_address": ip,
                "timestamp": datetime.now(tz=UTC).isoformat(),
                "details": details,
            }
        )


__all__ = ["InMemoryServiceTokenRepository"]
