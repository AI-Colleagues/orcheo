"""OAuth credential refresh and health validation service."""

from __future__ import annotations
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID
from orcheo.models import (
    CredentialAccessContext,
    CredentialHealthStatus,
    CredentialIssuancePolicy,
    CredentialKind,
    CredentialMetadata,
    CredentialTemplate,
    GovernanceAlertKind,
    OAuthTokenSecrets,
)
from orcheo.vault import BaseCredentialVault
from orcheo.vault.oauth.models import (
    CredentialHealthError,
    CredentialHealthGuard,
    CredentialHealthReport,
    CredentialHealthResult,
    OAuthProvider,
)
from orcheo.vault.oauth.policies import (
    should_refresh_tokens,
    validate_template_policy,
)
from orcheo.vault.oauth.processor import OAuthCredentialProcessor


class OAuthCredentialService(CredentialHealthGuard):
    """Coordinates OAuth token refresh and health validation operations."""

    def __init__(
        self,
        vault: BaseCredentialVault,
        *,
        token_ttl_seconds: int,
        providers: Mapping[str, OAuthProvider] | None = None,
        default_actor: str = "system",
    ) -> None:
        """Create the OAuth credential service with provider refresh hooks."""
        if token_ttl_seconds <= 0:
            msg = "token_ttl_seconds must be greater than zero"
            raise ValueError(msg)
        self._vault = vault
        self._providers: dict[str, OAuthProvider] = dict(providers or {})
        self._default_actor = default_actor
        self._refresh_margin = timedelta(seconds=token_ttl_seconds)
        self._processor = OAuthCredentialProcessor(
            vault=self._vault,
            providers=self._providers,
            refresh_margin=self._refresh_margin,
        )
        self._reports: dict[UUID, CredentialHealthReport] = {}

    def register_provider(self, provider: str, handler: OAuthProvider) -> None:
        """Register or replace the OAuth provider handler."""
        if not provider:
            msg = "provider cannot be empty"
            raise ValueError(msg)
        self._providers[provider] = handler

    def issue_from_template(
        self,
        *,
        template_id: UUID,
        secret: str,
        actor: str,
        name: str | None = None,
        scopes: Sequence[str] | None = None,
        context: CredentialAccessContext | None = None,
        oauth_tokens: OAuthTokenSecrets | None = None,
    ) -> CredentialMetadata:
        """Instantiate a credential using the provided template defaults."""
        access_context = context or CredentialAccessContext()
        template = self._vault.get_template(
            template_id=template_id, context=access_context
        )
        self._validate_template_policy(
            template.issuance_policy,
            oauth_tokens=oauth_tokens,
        )
        metadata = self._vault.create_credential(
            name=name or template.name,
            provider=template.provider,
            scopes=scopes or template.scopes,
            secret=secret,
            actor=actor,
            scope=template.scope,
            kind=template.kind,
            oauth_tokens=oauth_tokens,
            template_id=template.id,
            workspace_id=template.workspace_id
            or (
                str(access_context.workspace_id)
                if access_context.workspace_id is not None
                else None
            ),
        )
        self._vault.record_template_issuance(
            template_id=template.id,
            actor=actor,
            credential_id=metadata.id,
            context=access_context,
        )
        return metadata

    def is_workflow_healthy(self, workflow_id: UUID) -> bool:
        """Return True when the cached health report has no failures."""
        report = self._reports.get(workflow_id)
        return True if report is None else report.is_healthy

    def get_report(self, workflow_id: UUID) -> CredentialHealthReport | None:
        """Return the most recent credential health report for the workflow."""
        return self._reports.get(workflow_id)

    async def ensure_workflow_health(
        self,
        workflow_id: UUID,
        *,
        actor: str | None = None,
        workspace_id: str | None = None,
    ) -> CredentialHealthReport:
        """Evaluate and refresh credentials prior to workflow execution."""
        context = CredentialAccessContext(
            workflow_id=workflow_id,
            workspace_id=workspace_id,
        )
        credentials = self._vault.list_credentials(
            context=context,
            workspace_id=workspace_id,
        )
        actor_name = actor or self._default_actor
        results: list[CredentialHealthResult] = []

        for metadata in credentials:
            if metadata.kind is not CredentialKind.OAUTH:
                updated = self._vault.mark_health(
                    credential_id=metadata.id,
                    status=CredentialHealthStatus.HEALTHY,
                    reason=None,
                    actor=actor_name,
                    context=context,
                )
                results.append(self._build_result(updated))
                continue
            result = await self._processor.process(
                metadata,
                context=context,
                actor_name=actor_name,
            )
            results.append(result)

        report = CredentialHealthReport(
            workflow_id=workflow_id,
            results=results,
            checked_at=datetime.now(tz=UTC),
        )
        self._reports[workflow_id] = report
        return report

    def require_healthy(self, workflow_id: UUID) -> None:
        """Raise an error if the cached report deems the workflow unhealthy."""
        report = self._reports.get(workflow_id)
        if report is None or report.is_healthy:
            return

        raise CredentialHealthError(report)

    def _build_result(self, metadata: CredentialMetadata) -> CredentialHealthResult:
        return CredentialHealthResult(
            credential_id=metadata.id,
            name=metadata.name,
            provider=metadata.provider,
            status=metadata.health.status,
            last_checked_at=metadata.health.last_checked_at,
            failure_reason=metadata.health.failure_reason,
        )

    def _should_refresh(self, tokens: OAuthTokenSecrets | None) -> bool:
        return should_refresh_tokens(tokens, refresh_margin=self._refresh_margin)

    def _load_template_for_metadata(
        self,
        metadata: CredentialMetadata,
        context: CredentialAccessContext,
    ) -> CredentialTemplate | None:
        return self._processor.load_template_for_metadata(metadata, context)

    def _apply_rotation_policy(
        self,
        template: CredentialTemplate | None,
        metadata: CredentialMetadata,
        alerts_triggered: set[GovernanceAlertKind],
        *,
        context: CredentialAccessContext,
        actor_name: str,
    ) -> None:
        self._processor.apply_rotation_policy(
            template,
            metadata,
            alerts_triggered,
            context=context,
            actor_name=actor_name,
        )

    def _validate_template_policy(
        self,
        policy: CredentialIssuancePolicy,
        *,
        oauth_tokens: OAuthTokenSecrets | None,
    ) -> None:
        validate_template_policy(policy, oauth_tokens=oauth_tokens)


__all__ = ["OAuthCredentialService"]
