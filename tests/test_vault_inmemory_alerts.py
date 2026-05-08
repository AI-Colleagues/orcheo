"""Alert management tests for InMemoryCredentialVault."""

from __future__ import annotations
from uuid import uuid4
from orcheo.models import (
    AesGcmCredentialCipher,
    CredentialAccessContext,
    CredentialScope,
    GovernanceAlertKind,
    SecretGovernanceAlertSeverity,
)
from orcheo.vault import InMemoryCredentialVault


class BroadAlertVault(InMemoryCredentialVault):
    """Test helper that exposes every stored alert regardless of workspace."""

    def _iter_alerts(self, *, workspace_id: str | None = None):
        del workspace_id
        yield from (alert.model_copy(deep=True) for alert in self._alerts.values())


def test_record_alert_updates_existing_entries() -> None:
    cipher = AesGcmCredentialCipher(key="alert-update")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    metadata = vault.create_credential(
        name="Slack",
        provider="slack",
        scopes=["chat:write"],
        secret="client-secret",
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )

    first = vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="expires soon",
        actor="monitor",
        credential_id=metadata.id,
        context=CredentialAccessContext(workflow_id=workflow_id),
    )
    second = vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.CRITICAL,
        message="expired",
        actor="monitor",
        credential_id=metadata.id,
        context=CredentialAccessContext(workflow_id=workflow_id),
    )

    assert second.id == first.id
    assert second.message == "expired"
    assert second.audit_log[-1].action == "alert_updated"


def test_record_alert_uses_template_scope() -> None:
    cipher = AesGcmCredentialCipher(key="alert-template")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    template = vault.create_template(
        name="API",
        provider="api",
        scopes=["read"],
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )

    alert = vault.record_alert(
        kind=GovernanceAlertKind.ROTATION_OVERDUE,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="rotate",
        actor="ops",
        template_id=template.id,
        context=CredentialAccessContext(workflow_id=workflow_id),
    )

    assert alert.scope == template.scope


def test_record_alert_skips_acknowledged_entries() -> None:
    cipher = AesGcmCredentialCipher(key="alert-skip")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    metadata = vault.create_credential(
        name="Service",
        provider="service",
        scopes=["read"],
        secret="secret",
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )

    alert = vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="expiring",
        actor="ops",
        credential_id=metadata.id,
        context=CredentialAccessContext(workflow_id=workflow_id),
    )
    vault.acknowledge_alert(
        alert.id,
        actor="ops",
        context=CredentialAccessContext(workflow_id=workflow_id),
    )

    refreshed = vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.CRITICAL,
        message="expiring",
        actor="ops",
        credential_id=metadata.id,
        context=CredentialAccessContext(workflow_id=workflow_id),
    )

    assert refreshed.id != alert.id


def test_record_alert_ignores_non_matching_existing_entries() -> None:
    cipher = AesGcmCredentialCipher(key="alert-ignore")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    template = vault.create_template(
        name="API",
        provider="api",
        scopes=["read"],
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )
    template_alert = vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="template-alert",
        actor="ops",
        template_id=template.id,
        context=CredentialAccessContext(workflow_id=workflow_id),
    )

    metadata = vault.create_credential(
        name="Service",
        provider="svc",
        scopes=["read"],
        secret="secret",
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
        template_id=template.id,
    )

    credential_alert = vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.CRITICAL,
        message="credential-alert",
        actor="ops",
        credential_id=metadata.id,
        context=CredentialAccessContext(workflow_id=workflow_id),
    )

    assert credential_alert.id != template_alert.id


def test_list_alerts_filters_by_context_and_acknowledgement() -> None:
    cipher = AesGcmCredentialCipher(key="alert-list")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    template = vault.create_template(
        name="Restricted",
        provider="service",
        scopes=["read"],
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )
    restricted = vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="restricted",
        actor="ops",
        template_id=template.id,
        context=CredentialAccessContext(workflow_id=workflow_id),
    )
    _global_alert = vault.record_alert(
        kind=GovernanceAlertKind.VALIDATION_FAILED,
        severity=SecretGovernanceAlertSeverity.CRITICAL,
        message="global",
        actor="ops",
    )

    filtered = vault.list_alerts(
        context=CredentialAccessContext(workflow_id=uuid4()),
        include_acknowledged=False,
    )
    assert [alert.message for alert in filtered] == ["global"]

    vault.acknowledge_alert(
        restricted.id,
        actor="ops",
        context=CredentialAccessContext(workflow_id=workflow_id),
    )
    all_alerts = vault.list_alerts(
        context=CredentialAccessContext(workflow_id=workflow_id),
        include_acknowledged=True,
    )
    assert {alert.message for alert in all_alerts} == {"restricted", "global"}


def test_list_alerts_excludes_acknowledged_by_default() -> None:
    vault = InMemoryCredentialVault()
    alert = vault.record_alert(
        kind=GovernanceAlertKind.VALIDATION_FAILED,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="failure",
        actor="ops",
    )
    vault.acknowledge_alert(alert.id, actor="ops")

    assert vault.list_alerts() == []


def test_inmemory_iter_alerts_filters_workspace() -> None:
    vault = InMemoryCredentialVault()
    vault.record_alert(
        kind=GovernanceAlertKind.VALIDATION_FAILED,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="workspace-a",
        actor="ops",
        workspace_id="workspace-a",
    )
    vault.record_alert(
        kind=GovernanceAlertKind.VALIDATION_FAILED,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="workspace-b",
        actor="ops",
        workspace_id="workspace-b",
    )

    alerts = list(vault._iter_alerts(workspace_id="workspace-a"))

    assert [alert.message for alert in alerts] == ["workspace-a"]


def test_record_alert_skips_alerts_from_other_workspaces() -> None:
    vault = BroadAlertVault()
    vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="existing",
        actor="ops",
        workspace_id="workspace-b",
    )

    refreshed = vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.CRITICAL,
        message="new",
        actor="ops",
        workspace_id="workspace-a",
        context=CredentialAccessContext(workspace_id=uuid4()),
    )

    assert refreshed.message == "new"


def test_list_alerts_skips_alerts_from_other_workspaces() -> None:
    vault = BroadAlertVault()
    vault.record_alert(
        kind=GovernanceAlertKind.VALIDATION_FAILED,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="workspace-b",
        actor="ops",
        workspace_id="workspace-b",
    )

    assert (
        vault.list_alerts(context=CredentialAccessContext(workspace_id=uuid4())) == []
    )
