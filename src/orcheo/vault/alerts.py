"""Governance alert helpers shared across vault implementations."""

from __future__ import annotations
from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID
from orcheo.models import (
    CredentialAccessContext,
    CredentialScope,
    GovernanceAlertKind,
    SecretGovernanceAlert,
    SecretGovernanceAlertSeverity,
)
from orcheo.vault.errors import WorkflowScopeError


if TYPE_CHECKING:
    from orcheo.models import CredentialMetadata, CredentialTemplate  # pragma: no cover

    class _AlertDependencies(Protocol):
        def _get_metadata(
            self, *, credential_id: UUID, context: CredentialAccessContext
        ) -> CredentialMetadata: ...

        def _get_template(
            self, *, template_id: UUID, context: CredentialAccessContext
        ) -> CredentialTemplate: ...


class GovernanceAlertOperationsMixin:
    """Mixin implementing governance alert workflows."""

    def record_alert(
        self,
        *,
        kind: GovernanceAlertKind,
        severity: SecretGovernanceAlertSeverity,
        message: str,
        actor: str,
        credential_id: UUID | None = None,
        template_id: UUID | None = None,
        context: CredentialAccessContext | None = None,
        workspace_id: str | None = None,
    ) -> SecretGovernanceAlert:
        """Persist a governance alert tied to a credential or template."""
        access_context = context or CredentialAccessContext()
        workspace_id = (
            workspace_id if workspace_id is not None else access_context.workspace_id
        )
        scope = CredentialScope.unrestricted()
        resolver = cast("_AlertDependencies", self)
        if credential_id is not None:
            metadata = resolver._get_metadata(
                credential_id=credential_id, context=access_context
            )
            scope = metadata.scope
            template_id = template_id or metadata.template_id
            workspace_id = workspace_id or metadata.workspace_id
        elif template_id is not None:
            template = resolver._get_template(
                template_id=template_id, context=access_context
            )
            scope = template.scope
            workspace_id = workspace_id or template.workspace_id

        existing = None
        for alert in self._iter_alerts(workspace_id=workspace_id):
            if not self._alert_matches_workspace(alert.workspace_id, access_context):
                continue
            if not alert.scope.allows(access_context):
                continue
            if alert.is_acknowledged or alert.kind is not kind:
                continue
            matches_credential = alert.credential_id == credential_id
            matches_template = alert.template_id == template_id
            if matches_credential and matches_template:
                existing = alert
                break

        if existing is not None:
            existing.severity = severity
            existing.message = message
            existing.record_event(
                actor=actor,
                action="alert_updated",
                metadata={"severity": severity.value, "message": message},
            )
            self._persist_alert(existing)
            return existing.model_copy(deep=True)

        alert = SecretGovernanceAlert.create(
            scope=scope,
            kind=kind,
            severity=severity,
            message=message,
            actor=actor,
            credential_id=credential_id,
            template_id=template_id,
            workspace_id=workspace_id,
        )
        self._persist_alert(alert)
        return alert.model_copy(deep=True)

    def list_alerts(
        self,
        *,
        context: CredentialAccessContext | None = None,
        include_acknowledged: bool = False,
    ) -> list[SecretGovernanceAlert]:
        """Return governance alerts permitted for the caller."""
        access_context = context or CredentialAccessContext()
        workspace_id = access_context.workspace_id
        results: list[SecretGovernanceAlert] = []
        for alert in self._iter_alerts(workspace_id=workspace_id):
            if not self._alert_matches_workspace(alert.workspace_id, access_context):
                continue
            if not alert.scope.allows(access_context):
                continue
            if not include_acknowledged and alert.is_acknowledged:
                continue
            results.append(alert.model_copy(deep=True))
        return results

    def acknowledge_alert(
        self,
        alert_id: UUID,
        *,
        actor: str,
        context: CredentialAccessContext | None = None,
    ) -> SecretGovernanceAlert:
        """Mark the specified alert as acknowledged."""
        alert = self._get_alert(alert_id=alert_id, context=context)
        alert.acknowledge(actor=actor)
        self._persist_alert(alert)
        return alert.model_copy(deep=True)

    def resolve_alerts_for_credential(
        self,
        credential_id: UUID,
        *,
        actor: str,
    ) -> list[SecretGovernanceAlert]:
        """Acknowledge all alerts associated with the credential."""
        resolved: list[SecretGovernanceAlert] = []
        for alert in self._iter_alerts():
            if alert.credential_id != credential_id or alert.is_acknowledged:
                continue
            alert.acknowledge(actor=actor)
            self._persist_alert(alert)
            resolved.append(alert.model_copy(deep=True))
        return resolved

    def _get_alert(
        self,
        *,
        alert_id: UUID,
        context: CredentialAccessContext | None = None,
    ) -> SecretGovernanceAlert:
        alert = self._load_alert(alert_id)
        if context is None:
            if not alert.scope.is_unrestricted():
                msg = "Governance alert requires access context matching its scope."
                raise WorkflowScopeError(msg)
            access_context = CredentialAccessContext()
        else:
            access_context = context
        if not alert.scope.allows(access_context):
            msg = "Governance alert cannot be accessed with the provided context."
            raise WorkflowScopeError(msg)
        return alert

    def _persist_alert(self, alert: SecretGovernanceAlert) -> None:  # pragma: no cover
        raise NotImplementedError

    def _load_alert(self, alert_id: UUID) -> SecretGovernanceAlert:  # pragma: no cover
        raise NotImplementedError

    def _iter_alerts(
        self, *, workspace_id: str | None = None
    ) -> Iterable[SecretGovernanceAlert]:  # pragma: no cover
        raise NotImplementedError

    def _remove_alert(self, alert_id: UUID) -> None:  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def _alert_matches_workspace(
        alert_workspace_id: str | None, context: CredentialAccessContext
    ) -> bool:
        """Return whether an alert belongs to the active workspace."""
        if context.workspace_id is None:
            return True
        return alert_workspace_id is None or alert_workspace_id == str(
            context.workspace_id
        )


__all__ = ["GovernanceAlertOperationsMixin"]
