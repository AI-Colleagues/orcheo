"""Protocol definition for service token repositories."""

from __future__ import annotations
from datetime import datetime
from typing import Any, Protocol
from orcheo_backend.app.authentication import ServiceTokenRecord


class ServiceTokenRepository(Protocol):
    """Abstract interface for service token persistence."""

    async def list_all(self) -> list[ServiceTokenRecord]:
        """Return all service token records (including revoked/expired)."""
        ...  # pragma: no cover

    async def list_active(
        self, *, now: datetime | None = None
    ) -> list[ServiceTokenRecord]:
        """Return all active (non-revoked, non-expired) service token records."""
        ...  # pragma: no cover

    async def list_for_tenant(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> list[ServiceTokenRecord]:
        """Return active records bound to *tenant_id*.

        Default implementations may filter ``list_active`` by tenant; specialised
        backends can push the predicate into SQL via the
        ``(tenant_id, identifier)`` index.
        """
        ...  # pragma: no cover

    async def find_by_id(self, identifier: str) -> ServiceTokenRecord | None:
        """Look up a service token by its identifier."""
        ...  # pragma: no cover

    async def find_by_hash(self, secret_hash: str) -> ServiceTokenRecord | None:
        """Look up a service token by its SHA256 hash."""
        ...  # pragma: no cover

    async def create(self, record: ServiceTokenRecord) -> ServiceTokenRecord:
        """Store a new service token record."""
        ...  # pragma: no cover

    async def update(self, record: ServiceTokenRecord) -> ServiceTokenRecord:
        """Update an existing service token record."""
        ...  # pragma: no cover

    async def delete(self, identifier: str) -> None:
        """Remove a service token record from storage."""
        ...  # pragma: no cover

    async def record_usage(
        self,
        token_id: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Track token usage for analytics and audit."""
        ...  # pragma: no cover

    async def get_audit_log(
        self, token_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Retrieve audit log entries for a token."""
        ...  # pragma: no cover


__all__ = ["ServiceTokenRepository"]
