"""Reusable credential template definitions."""

from __future__ import annotations
from collections.abc import Sequence
from uuid import UUID
from pydantic import Field, field_validator
from orcheo.models.base import TimestampedAuditModel
from orcheo.models.credential_crypto import CredentialCipher
from orcheo.models.credential_health import CredentialIssuancePolicy
from orcheo.models.credential_metadata import CredentialKind, CredentialMetadata
from orcheo.models.credential_oauth import OAuthTokenSecrets
from orcheo.models.credential_scope import CredentialScope


__all__ = ["CredentialTemplate"]


class CredentialTemplate(TimestampedAuditModel):
    """Reusable blueprint describing credential defaults and policies."""

    name: str
    provider: str
    workspace_id: str | None = None
    description: str | None = None
    scopes: list[str] = Field(default_factory=list)
    scope: CredentialScope = Field(default_factory=CredentialScope.unrestricted)
    kind: CredentialKind = Field(default=CredentialKind.SECRET)
    issuance_policy: CredentialIssuancePolicy = Field(
        default_factory=CredentialIssuancePolicy
    )

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
        actor: str,
        description: str | None = None,
        scope: CredentialScope | None = None,
        kind: CredentialKind = CredentialKind.SECRET,
        issuance_policy: CredentialIssuancePolicy | None = None,
        workspace_id: str | None = None,
    ) -> CredentialTemplate:
        """Return a new template populated with the provided defaults."""
        template = cls(
            name=name,
            provider=provider,
            workspace_id=workspace_id,
            description=description,
            scopes=list(scopes),
            scope=scope or CredentialScope.unrestricted(),
            kind=kind,
            issuance_policy=issuance_policy or CredentialIssuancePolicy(),
        )
        template.record_event(actor=actor, action="template_created")
        return template

    def record_issuance(self, *, actor: str, credential_id: UUID) -> None:
        """Append an audit entry describing credential issuance."""
        self.record_event(
            actor=actor,
            action="credential_issued",
            metadata={"credential_id": str(credential_id)},
        )

    def instantiate_metadata(
        self,
        *,
        name: str | None,
        secret: str,
        cipher: CredentialCipher,
        actor: str,
        scopes: Sequence[str] | None = None,
        oauth_tokens: OAuthTokenSecrets | None = None,
    ) -> CredentialMetadata:
        """Create credential metadata honouring template defaults."""
        return CredentialMetadata.create(
            name=name or self.name,
            provider=self.provider,
            scopes=scopes or self.scopes,
            secret=secret,
            cipher=cipher,
            actor=actor,
            scope=self.scope,
            kind=self.kind,
            oauth_tokens=oauth_tokens,
            template_id=self.id,
            workspace_id=self.workspace_id,
        )
