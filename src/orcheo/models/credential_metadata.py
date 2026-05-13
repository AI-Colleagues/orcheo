"""Credential metadata, templates, and associated helpers."""

from __future__ import annotations
from collections.abc import MutableMapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID
from pydantic import Field, field_validator
from orcheo.models.base import TimestampedAuditModel, _utcnow
from orcheo.models.credential_crypto import CredentialCipher, EncryptionEnvelope
from orcheo.models.credential_health import (
    CredentialHealth,
    CredentialHealthStatus,
)
from orcheo.models.credential_oauth import OAuthTokenPayload, OAuthTokenSecrets
from orcheo.models.credential_scope import CredentialScope


__all__ = [
    "CredentialKind",
    "CredentialMetadata",
]


class CredentialKind(str, Enum):
    """Enumerates supported credential persistence strategies."""

    SECRET = "secret"
    OAUTH = "oauth"


class CredentialMetadata(TimestampedAuditModel):
    """Metadata describing encrypted credentials with configurable scope."""

    name: str
    provider: str
    workspace_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    scope: CredentialScope = Field(default_factory=CredentialScope.unrestricted)
    encryption: EncryptionEnvelope
    kind: CredentialKind = Field(default=CredentialKind.SECRET)
    oauth_tokens: OAuthTokenPayload | None = None
    health: CredentialHealth = Field(default_factory=CredentialHealth)
    last_rotated_at: datetime | None = None
    template_id: UUID | None = None

    @field_validator("scopes", mode="after")
    @classmethod
    def _normalize_scopes(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for scope in value:
            candidate = scope.strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                normalized.append(candidate)
        return normalized

    @classmethod
    def create(
        cls,
        *,
        name: str,
        provider: str,
        scopes: Sequence[str],
        secret: str,
        cipher: CredentialCipher,
        actor: str,
        scope: CredentialScope | None = None,
        kind: CredentialKind = CredentialKind.SECRET,
        oauth_tokens: OAuthTokenSecrets | None = None,
        template_id: UUID | None = None,
        workspace_id: str | None = None,
    ) -> CredentialMetadata:
        """Construct a credential metadata record with encrypted secret."""
        encryption = cipher.encrypt(secret)
        metadata = cls(
            name=name,
            provider=provider,
            workspace_id=workspace_id,
            scopes=list(scopes),
            scope=scope or CredentialScope.unrestricted(),
            encryption=encryption,
            kind=kind,
            oauth_tokens=OAuthTokenPayload.from_secrets(
                cipher=cipher, secrets=oauth_tokens
            )
            if kind is CredentialKind.OAUTH
            else None,
            template_id=template_id,
        )
        metadata.record_event(actor=actor, action="credential_created")
        metadata.last_rotated_at = metadata.created_at
        return metadata

    def rotate_secret(
        self,
        *,
        secret: str,
        cipher: CredentialCipher,
        actor: str,
    ) -> None:
        """Rotate the secret value and update audit metadata."""
        self.encryption = cipher.encrypt(secret)
        now = _utcnow()
        self.last_rotated_at = now
        self.record_event(actor=actor, action="credential_rotated")
        if self.kind is CredentialKind.OAUTH:
            self.health.update(status=CredentialHealthStatus.UNKNOWN)

    def reveal(self, *, cipher: CredentialCipher) -> str:
        """Decrypt and return the credential secret."""
        return self.encryption.decrypt(cipher)

    def reveal_oauth_tokens(
        self, *, cipher: CredentialCipher
    ) -> OAuthTokenSecrets | None:
        """Return decrypted OAuth tokens when available."""
        if self.oauth_tokens is None:
            return None
        return self.oauth_tokens.reveal(cipher=cipher)

    def update_oauth_tokens(
        self,
        *,
        cipher: CredentialCipher,
        tokens: OAuthTokenSecrets | None,
        actor: str | None = None,
    ) -> None:
        """Persist updated OAuth tokens and reset cached health information."""
        if self.kind is not CredentialKind.OAUTH:
            msg = "OAuth tokens can only be updated for OAuth credentials."
            raise ValueError(msg)
        payload = OAuthTokenPayload.from_secrets(cipher=cipher, secrets=tokens)
        if (
            payload.access_token is None
            and payload.refresh_token is None
            and payload.expires_at is None
        ):
            self.oauth_tokens = None
        else:
            self.oauth_tokens = payload
        self.health.update(status=CredentialHealthStatus.UNKNOWN)
        self.record_event(
            actor=actor or "system",
            action="credential_oauth_tokens_updated",
        )

    def mark_health(
        self,
        *,
        status: CredentialHealthStatus,
        reason: str | None,
        actor: str | None = None,
    ) -> None:
        """Record the latest credential health evaluation for the metadata."""
        self.health.update(status=status, reason=reason)
        self.record_event(
            actor=actor or "system",
            action="credential_health_marked",
            metadata={
                "status": status.value,
                "reason": reason,
            },
        )

    def redact(self) -> MutableMapping[str, Any]:
        """Return a redacted representation suitable for logs."""
        return {
            "id": str(self.id),
            "name": self.name,
            "provider": self.provider,
            "scopes": list(self.scopes),
            "scope": self.scope.model_dump(),
            "kind": self.kind.value,
            "last_rotated_at": self.last_rotated_at.isoformat()
            if self.last_rotated_at
            else None,
            "template_id": str(self.template_id) if self.template_id else None,
            "encryption": {
                "algorithm": self.encryption.algorithm,
                "key_id": self.encryption.key_id,
            },
            "oauth_tokens": self.oauth_tokens.redact() if self.oauth_tokens else None,
            "health": self.health.redact(),
        }
