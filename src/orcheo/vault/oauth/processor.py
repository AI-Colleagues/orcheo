"""Processing helpers for OAuth credential health checks."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from orcheo.models import (
    CredentialAccessContext,
    CredentialHealthStatus,
    CredentialMetadata,
    CredentialTemplate,
    GovernanceAlertKind,
    OAuthTokenSecrets,
    SecretGovernanceAlertSeverity,
)
from orcheo.vault import BaseCredentialVault
from orcheo.vault.oauth.models import (
    CredentialHealthResult,
    OAuthProvider,
    OAuthValidationResult,
)
from orcheo.vault.oauth.policies import should_refresh_tokens


class OAuthCredentialProcessor:
    """Encapsulates provider specific processing for OAuth credentials."""

    def __init__(
        self,
        *,
        vault: BaseCredentialVault,
        providers: Mapping[str, OAuthProvider],
        refresh_margin: timedelta,
    ) -> None:
        """Store shared dependencies for credential processing."""
        self._vault = vault
        self._providers = providers
        self._refresh_margin = refresh_margin

    async def process(
        self,
        metadata: CredentialMetadata,
        *,
        context: CredentialAccessContext,
        actor_name: str,
    ) -> CredentialHealthResult:
        """Refresh and validate a single OAuth credential."""
        provider = self._providers.get(metadata.provider)
        if provider is None:
            updated = await asyncio.to_thread(
                self._vault.mark_health,
                credential_id=metadata.id,
                status=CredentialHealthStatus.UNHEALTHY,
                reason=f"No OAuth provider registered for '{metadata.provider}'",
                actor=actor_name,
                context=context,
            )
            await asyncio.to_thread(
                self._record_validation_failure,
                metadata=metadata,
                actor_name=actor_name,
                context=context,
                message=f"No provider registered for {metadata.provider}.",
            )
            return self._build_result(updated)

        metadata_copy = metadata
        tokens = metadata_copy.reveal_oauth_tokens(cipher=self._vault.cipher)
        alerts_triggered: set[GovernanceAlertKind] = set()
        template = await asyncio.to_thread(
            self.load_template_for_metadata, metadata, context
        )
        await asyncio.to_thread(
            self.apply_rotation_policy,
            template,
            metadata,
            alerts_triggered,
            context=context,
            actor_name=actor_name,
        )

        try:
            if should_refresh_tokens(tokens, refresh_margin=self._refresh_margin):
                refreshed = await provider.refresh_tokens(metadata_copy, tokens)
                if refreshed is not None:
                    metadata_copy = await asyncio.to_thread(
                        self._vault.update_oauth_tokens,
                        credential_id=metadata.id,
                        tokens=refreshed,
                        actor=actor_name,
                        context=context,
                    )
                    tokens = metadata_copy.reveal_oauth_tokens(
                        cipher=self._vault.cipher
                    )
        except Exception as exc:  # pragma: no cover - provider errors handled
            updated = await asyncio.to_thread(
                self._vault.mark_health,
                credential_id=metadata.id,
                status=CredentialHealthStatus.UNHEALTHY,
                reason=str(exc),
                actor=actor_name,
                context=context,
            )
            alerts_triggered.add(GovernanceAlertKind.VALIDATION_FAILED)
            await asyncio.to_thread(
                self._record_validation_failure,
                metadata=metadata,
                actor_name=actor_name,
                context=context,
                message=str(exc),
            )
            return self._build_result(updated)

        try:
            validation = await provider.validate_tokens(metadata_copy, tokens)
        except Exception as exc:  # pragma: no cover - provider errors handled
            validation = OAuthValidationResult(
                status=CredentialHealthStatus.UNHEALTHY,
                failure_reason=str(exc),
            )

        updated = await asyncio.to_thread(
            self._vault.mark_health,
            credential_id=metadata.id,
            status=validation.status,
            reason=validation.failure_reason,
            actor=actor_name,
            context=context,
        )

        await asyncio.to_thread(
            self._apply_token_expiry_alert,
            metadata,
            tokens,
            alerts_triggered,
            context=context,
            actor_name=actor_name,
        )

        result = self._build_result(updated)

        if validation.status is CredentialHealthStatus.UNHEALTHY:
            alerts_triggered.add(GovernanceAlertKind.VALIDATION_FAILED)
            await asyncio.to_thread(
                self._record_validation_failure,
                metadata=metadata,
                actor_name=actor_name,
                context=context,
                message=validation.failure_reason or "Credential validation failed",
            )
        elif not alerts_triggered:
            await asyncio.to_thread(
                self._vault.resolve_alerts_for_credential, updated.id, actor=actor_name
            )

        return result

    def _build_result(self, metadata: CredentialMetadata) -> CredentialHealthResult:
        return CredentialHealthResult(
            credential_id=metadata.id,
            name=metadata.name,
            provider=metadata.provider,
            status=metadata.health.status,
            last_checked_at=metadata.health.last_checked_at,
            failure_reason=metadata.health.failure_reason,
        )

    def load_template_for_metadata(
        self,
        metadata: CredentialMetadata,
        context: CredentialAccessContext,
    ) -> CredentialTemplate | None:
        """Return the template associated with the provided credential."""
        if metadata.template_id is None:
            return None
        try:
            return self._vault.get_template(
                template_id=metadata.template_id,
                context=context,
            )
        except Exception:  # pragma: no cover - missing template should not block
            return None

    def apply_rotation_policy(
        self,
        template: CredentialTemplate | None,
        metadata: CredentialMetadata,
        alerts_triggered: set[GovernanceAlertKind],
        *,
        context: CredentialAccessContext,
        actor_name: str,
    ) -> None:
        """Record an overdue rotation alert if the policy requires it."""
        if template and template.issuance_policy.requires_rotation(
            last_rotated_at=metadata.last_rotated_at
        ):
            alerts_triggered.add(GovernanceAlertKind.ROTATION_OVERDUE)
            self._vault.record_alert(
                kind=GovernanceAlertKind.ROTATION_OVERDUE,
                severity=SecretGovernanceAlertSeverity.WARNING,
                message="Credential rotation is overdue per template policy.",
                actor=actor_name,
                credential_id=metadata.id,
                template_id=metadata.template_id,
                context=context,
            )

    def _apply_token_expiry_alert(
        self,
        metadata: CredentialMetadata,
        tokens: OAuthTokenSecrets | None,
        alerts_triggered: set[GovernanceAlertKind],
        *,
        context: CredentialAccessContext,
        actor_name: str,
    ) -> None:
        if tokens is None or tokens.expires_at is None:
            return
        now = datetime.now(tz=UTC)
        if tokens.expires_at <= now + self._refresh_margin:
            alerts_triggered.add(GovernanceAlertKind.TOKEN_EXPIRING)
            self._vault.record_alert(
                kind=GovernanceAlertKind.TOKEN_EXPIRING,
                severity=SecretGovernanceAlertSeverity.WARNING,
                message=f"Token expires at {tokens.expires_at.isoformat()}",
                actor=actor_name,
                credential_id=metadata.id,
                context=context,
            )

    def _record_validation_failure(
        self,
        *,
        metadata: CredentialMetadata,
        actor_name: str,
        context: CredentialAccessContext,
        message: str,
    ) -> None:
        self._vault.record_alert(
            kind=GovernanceAlertKind.VALIDATION_FAILED,
            severity=SecretGovernanceAlertSeverity.CRITICAL,
            message=message,
            actor=actor_name,
            credential_id=metadata.id,
            context=context,
        )


__all__ = ["OAuthCredentialProcessor"]
