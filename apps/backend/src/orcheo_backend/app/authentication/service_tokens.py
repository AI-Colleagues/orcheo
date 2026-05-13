from __future__ import annotations
import hashlib
import hmac
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from fastapi import status
from .errors import AuthenticationError
from .telemetry import auth_telemetry


@dataclass(frozen=True)
class ServiceTokenRecord:
    """Configuration describing a hashed service token."""

    identifier: str
    secret_hash: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    workspace_ids: frozenset[str] = field(default_factory=frozenset)
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    rotation_expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    rotated_to: str | None = None
    last_used_at: datetime | None = None
    use_count: int = 0
    workspace_id: str | None = None

    def matches(self, token: str) -> bool:
        """Return True when the provided token matches the stored hash."""
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(self.secret_hash, digest)

    def is_revoked(self) -> bool:
        """Return True when the token has been revoked."""
        return self.revoked_at is not None

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Return True when the token has passed its expiry timestamp."""
        if self.expires_at is None:
            return False
        reference = now or datetime.now(tz=UTC)
        return reference >= self.expires_at

    def is_active(self, *, now: datetime | None = None) -> bool:
        """Return True when the token is neither expired nor revoked."""
        return not self.is_revoked() and not self.is_expired(now=now)


class ServiceTokenManager:
    """Manage lifecycle of service tokens with database persistence."""

    def __init__(
        self,
        repository: Any,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize the manager with a token repository."""
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._cache: dict[str, ServiceTokenRecord] = {}
        self._cache_expires_at: datetime | None = None
        self._cache_ttl = timedelta(seconds=30)

    async def _get_cache(self) -> dict[str, ServiceTokenRecord]:
        """Return cached active tokens, refreshing if stale."""
        now = self._clock()
        if self._cache and self._cache_expires_at and now < self._cache_expires_at:
            return self._cache

        active_records = await self._repository.list_active(now=now)
        self._cache = {record.identifier: record for record in active_records}
        self._cache_expires_at = now + self._cache_ttl
        return self._cache

    def _invalidate_cache(self) -> None:
        """Clear the token cache to force reload."""
        self._cache.clear()
        self._cache_expires_at = None

    async def all(self) -> tuple[ServiceTokenRecord, ...]:
        """Return all active service token records."""
        cache = await self._get_cache()
        return tuple(cache.values())

    async def authenticate(self, token: str) -> ServiceTokenRecord:
        """Return the record for ``token`` or raise an AuthenticationError."""
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = await self._repository.find_by_hash(digest)

        if record is None or not record.matches(token):
            raise AuthenticationError("Invalid bearer token", code="auth.invalid_token")

        check_time = self._clock()
        if record.is_revoked():
            raise AuthenticationError(
                "Service token has been revoked",
                code="auth.token_revoked",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if record.is_expired(now=check_time):
            raise AuthenticationError(
                "Service token has expired",
                code="auth.token_expired",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        await self._repository.record_usage(record.identifier)
        usage_time = self._clock()
        updated_record = replace(
            record,
            last_used_at=usage_time,
            use_count=record.use_count + 1,
        )
        if record.identifier in self._cache:
            self._cache[record.identifier] = updated_record

        return updated_record

    async def mint(
        self,
        *,
        identifier: str | None = None,
        scopes: Iterable[str] = (),
        workspace_ids: Iterable[str] = (),
        expires_in: timedelta | int | None = None,
        workspace_id: str | None = None,
    ) -> tuple[str, ServiceTokenRecord]:
        """Mint a new service token and return the raw secret and record."""
        secret = secrets.token_urlsafe(32)
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        now = self._clock()
        if expires_in is None:
            expires_at: datetime | None = None
        elif isinstance(expires_in, timedelta):
            expires_at = now + expires_in
        else:
            expires_at = now + timedelta(seconds=int(expires_in))

        record = ServiceTokenRecord(
            identifier=identifier or digest[:8],
            secret_hash=digest,
            scopes=frozenset(scopes),
            workspace_ids=frozenset(workspace_ids),
            issued_at=now,
            expires_at=expires_at,
            workspace_id=workspace_id,
        )
        await self._repository.create(record)
        await self._repository.record_audit_event(record.identifier, "created")
        self._invalidate_cache()
        auth_telemetry.record_service_token_event("mint", record)
        return secret, record

    async def rotate(
        self,
        identifier: str,
        *,
        overlap_seconds: int = 300,
        expires_in: timedelta | int | None = None,
    ) -> tuple[str, ServiceTokenRecord]:
        """Rotate ``identifier`` and return the replacement token."""
        record = await self._repository.find_by_id(identifier)
        if record is None:
            raise KeyError(identifier)

        now = self._clock()
        overlap = max(int(overlap_seconds), 0)
        secret, new_record = await self.mint(
            scopes=record.scopes,
            workspace_ids=record.workspace_ids,
            expires_in=expires_in,
            workspace_id=record.workspace_id,
        )
        rotation_expires_at = (
            now + timedelta(seconds=overlap) if overlap else record.rotation_expires_at
        )
        updated = replace(
            record,
            rotation_expires_at=rotation_expires_at,
            expires_at=self._calculate_rotation_expiry(record, now, overlap),
            rotated_to=new_record.identifier,
        )
        await self._repository.update(updated)
        await self._repository.record_audit_event(identifier, "rotated")
        self._invalidate_cache()
        auth_telemetry.record_service_token_event("rotate", updated)
        return secret, new_record

    async def revoke(
        self, identifier: str, *, reason: str | None = None
    ) -> ServiceTokenRecord:
        """Revoke ``identifier`` immediately and return the updated record."""
        record = await self._repository.find_by_id(identifier)
        if record is None:
            raise KeyError(identifier)
        updated = replace(
            record,
            revoked_at=self._clock(),
            revocation_reason=reason,
        )
        await self._repository.update(updated)
        await self._repository.record_audit_event(
            identifier, "revoked", details={"reason": reason} if reason else None
        )
        self._invalidate_cache()
        auth_telemetry.record_service_token_event("revoke", updated)
        return updated

    @staticmethod
    def _calculate_rotation_expiry(
        record: ServiceTokenRecord, now: datetime, overlap_seconds: int
    ) -> datetime | None:
        if overlap_seconds == 0:
            return record.expires_at
        overlap_expiry = now + timedelta(seconds=overlap_seconds)
        if record.expires_at is None:
            return overlap_expiry
        return min(record.expires_at, overlap_expiry)
