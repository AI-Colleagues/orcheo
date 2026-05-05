"""Credential resolver implementation and helpers."""

from __future__ import annotations
from typing import Any
from uuid import UUID
from orcheo.models import (
    CredentialAccessContext,
    CredentialKind,
    CredentialMetadata,
    OAuthTokenSecrets,
)
from orcheo.vault import BaseCredentialVault, CredentialNotFoundError
from .exceptions import (
    CredentialReferenceNotFoundError,
    DuplicateCredentialReferenceError,
    UnknownCredentialPayloadError,
)
from .references import CredentialReference


class CredentialResolver:
    """Resolve credential references against a vault with per-run caching."""

    def __init__(
        self,
        vault: BaseCredentialVault,
        *,
        context: CredentialAccessContext | None = None,
    ) -> None:
        """Create a resolver bound to ``vault`` and an optional access context."""
        self._vault = vault
        self._context = context
        self._metadata_by_id: dict[UUID, CredentialMetadata] | None = None
        self._metadata_by_name: dict[str, list[CredentialMetadata]] | None = None
        self._secret_cache: dict[UUID, Any] = {}
        self._oauth_cache: dict[UUID, OAuthTokenSecrets | None] = {}

    def resolve(self, reference: CredentialReference) -> Any:
        """Return the concrete payload referenced by ``reference``."""
        metadata = self._locate_metadata(reference.identifier)
        return self._extract_payload(metadata, reference.payload_path)

    def _locate_metadata(self, identifier: str) -> CredentialMetadata:
        metadata_by_id, metadata_by_name = self._load_metadata_index()
        try:
            credential_id = UUID(identifier)
        except ValueError:
            credential_id = None

        if credential_id is not None:
            metadata = metadata_by_id.get(credential_id)
            if metadata is not None:
                return metadata

        normalized = identifier.strip()
        matches = metadata_by_name.get(normalized, []) if metadata_by_name else []
        if not matches:
            msg = f"Credential '{identifier}' was not found in the configured vault"
            raise CredentialReferenceNotFoundError(msg)
        if len(matches) > 1:
            msg = (
                "Multiple credentials share the name "
                f"'{identifier}'. Use the credential UUID instead."
            )
            raise DuplicateCredentialReferenceError(msg)
        return matches[0]

    def _load_metadata_index(
        self,
    ) -> tuple[dict[UUID, CredentialMetadata], dict[str, list[CredentialMetadata]]]:
        if self._metadata_by_id is not None and self._metadata_by_name is not None:
            return self._metadata_by_id, self._metadata_by_name
        try:
            tenant_id = self._context.tenant_id if self._context is not None else None
            metadata_items = self._vault.list_credentials(
                context=self._context,
                tenant_id=tenant_id,
            )
        except CredentialNotFoundError as exc:  # pragma: no cover - defensive
            raise CredentialReferenceNotFoundError(str(exc)) from exc
        by_id: dict[UUID, CredentialMetadata] = {}
        by_name: dict[str, list[CredentialMetadata]] = {}
        for metadata in metadata_items:
            by_id[metadata.id] = metadata
            by_name.setdefault(metadata.name, []).append(metadata)
        self._metadata_by_id = by_id
        self._metadata_by_name = by_name
        return by_id, by_name

    def _extract_payload(
        self, metadata: CredentialMetadata, payload_path: tuple[str, ...]
    ) -> Any:
        if not payload_path:
            return self._resolve_secret(metadata)
        head, *tail = payload_path
        if head.lower() == "secret":
            if tail:
                msg = "Secret payload does not support nested attributes"
                raise UnknownCredentialPayloadError(msg)
            return self._resolve_secret(metadata)
        if head.lower() == "oauth":
            tokens = self._resolve_oauth_tokens(metadata)
            if tokens is None:
                return None
            if not tail:
                return tokens.model_copy(deep=True)
            current: Any = tokens
            for attribute in tail:
                if not hasattr(current, attribute):
                    available = [
                        attr
                        for attr in ("access_token", "refresh_token", "expires_at")
                        if hasattr(current, attr)
                    ]
                    available_list = ", ".join(available) or "<none>"
                    msg = (
                        "OAuth token payload does not expose attribute "
                        f"'{attribute}'. Available: {available_list}"
                    )
                    raise UnknownCredentialPayloadError(msg)
                current = getattr(current, attribute)
            return current
        msg = (
            "Credential placeholder requested unsupported payload segment "
            f"'{payload_path[0]}'"
        )
        raise UnknownCredentialPayloadError(msg)

    def _resolve_secret(self, metadata: CredentialMetadata) -> Any:
        cached = self._secret_cache.get(metadata.id)
        if cached is not None:
            return cached
        secret = self._vault.reveal_secret(
            credential_id=metadata.id, context=self._context
        )
        self._secret_cache[metadata.id] = secret
        return secret

    def _resolve_oauth_tokens(
        self, metadata: CredentialMetadata
    ) -> OAuthTokenSecrets | None:
        if metadata.kind is not CredentialKind.OAUTH:
            msg = "OAuth payload requested for credential that is not of kind 'oauth'"
            raise UnknownCredentialPayloadError(msg)
        cached = self._oauth_cache.get(metadata.id)
        if cached is not None:
            return cached.model_copy(deep=True)
        tokens = metadata.reveal_oauth_tokens(cipher=self._vault.cipher)
        self._oauth_cache[metadata.id] = tokens
        if tokens is None:
            return None
        return tokens.model_copy(deep=True)

    def persist_refreshed_tokens(
        self,
        identifier: str,
        *,
        new_access_token: str,
        new_refresh_token: str | None = None,
        fallback_refresh_token: str | None = None,
        actor: str = "system",
    ) -> bool:
        """Write refreshed OAuth tokens back to the vault.

        Locates the credential by *identifier* (name or UUID) and updates it:
        - OAUTH kind: replaces ``access_token`` and (if available) ``refresh_token``.
        - SECRET kind: rotates the stored secret to *new_access_token*.

        Returns ``True`` when the credential was found and updated, ``False`` when it
        could not be located (e.g. custom credential name).  Other vault errors are
        propagated to the caller.
        """
        try:
            metadata = self._locate_metadata(identifier)
        except (CredentialReferenceNotFoundError, DuplicateCredentialReferenceError):
            return False
        if metadata.kind is CredentialKind.OAUTH:
            tokens = OAuthTokenSecrets(
                access_token=new_access_token,
                refresh_token=new_refresh_token or fallback_refresh_token,
            )
            self._vault.update_oauth_tokens(
                credential_id=metadata.id,
                tokens=tokens,
                actor=actor,
                context=self._context,
            )
        else:
            self._vault.update_credential(
                credential_id=metadata.id,
                actor=actor,
                secret=new_access_token,
                context=self._context,
            )
        return True


__all__ = ["CredentialResolver"]
