"""Template and alert lifecycle tests for InMemoryCredentialVault."""

from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.models import (
    AesGcmCredentialCipher,
    CredentialAccessContext,
    CredentialIssuancePolicy,
    CredentialKind,
    CredentialScope,
    GovernanceAlertKind,
    SecretGovernanceAlertSeverity,
)
from orcheo.vault import (
    CredentialTemplateNotFoundError,
    GovernanceAlertNotFoundError,
    InMemoryCredentialVault,
    WorkflowScopeError,
)


def test_vault_manages_templates_and_alerts() -> None:
    cipher = AesGcmCredentialCipher(key="template-test")
    vault = InMemoryCredentialVault(cipher=cipher)

    template = vault.create_template(
        name="Slack",
        provider="slack",
        scopes=["chat:write"],
        actor="alice",
        description="Slack bot",
        kind=CredentialKind.OAUTH,
        issuance_policy=CredentialIssuancePolicy(rotation_period_days=30),
    )

    templates = vault.list_templates()
    assert [item.id for item in templates] == [template.id]

    updated = vault.update_template(
        template.id,
        actor="alice",
        scopes=["chat:write", "chat:read"],
        description="Updated",
    )
    assert updated.scopes == ["chat:write", "chat:read"]
    assert updated.description == "Updated"

    fetched = vault.get_template(template_id=template.id)
    assert fetched.id == template.id

    alert = vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="Token expiring",
        actor="monitor",
        credential_id=None,
        template_id=template.id,
    )
    alerts = vault.list_alerts()
    assert alerts and alerts[0].id == alert.id

    acknowledged = vault.acknowledge_alert(alert.id, actor="alice")
    assert acknowledged.is_acknowledged

    all_alerts = vault.list_alerts(include_acknowledged=True)
    assert all_alerts[0].is_acknowledged

    vault.delete_template(template.id)
    with pytest.raises(CredentialTemplateNotFoundError):
        vault.get_template(template_id=template.id)

    with pytest.raises(GovernanceAlertNotFoundError):
        vault.acknowledge_alert(alert.id, actor="bob")


def test_template_update_tracks_all_changes() -> None:
    cipher = AesGcmCredentialCipher(key="template-update")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    template = vault.create_template(
        name="Service",
        provider="service",
        scopes=["read"],
        actor="alice",
        kind="oauth",
    )

    updated_scope = CredentialScope.for_workflows(workflow_id)
    new_policy = CredentialIssuancePolicy(
        require_refresh_token=True,
        rotation_period_days=90,
    )
    updated = vault.update_template(
        template.id,
        actor="alice",
        name="Service Prod",
        description="prod",
        scopes=["read", "write"],
        scope=updated_scope,
        kind="secret",
        issuance_policy=new_policy,
    )

    assert updated.name == "Service Prod"
    persisted = vault.get_template(
        template_id=template.id,
        context=CredentialAccessContext(workflow_id=workflow_id),
    )
    assert persisted.issuance_policy.rotation_period_days == 90
    last_event = persisted.audit_log[-1]
    assert last_event.action == "template_updated"
    assert {
        "name",
        "description",
        "scopes",
        "scope",
        "kind",
        "issuance_policy",
    }.issubset(last_event.metadata.keys())


def test_template_update_without_changes_is_noop() -> None:
    cipher = AesGcmCredentialCipher(key="template-noop")
    vault = InMemoryCredentialVault(cipher=cipher)
    template = vault.create_template(
        name="Slack",
        provider="slack",
        scopes=["chat:write"],
        actor="alice",
    )

    updated = vault.update_template(template.id, actor="alice")
    assert updated.audit_log == template.audit_log


def test_template_update_ignores_identical_payloads() -> None:
    cipher = AesGcmCredentialCipher(key="template-same")
    vault = InMemoryCredentialVault(cipher=cipher)
    template = vault.create_template(
        name="Service",
        provider="service",
        scopes=["read"],
        actor="alice",
        issuance_policy=CredentialIssuancePolicy(rotation_period_days=30),
    )

    same_policy = CredentialIssuancePolicy(
        rotation_period_days=template.issuance_policy.rotation_period_days,
        require_refresh_token=template.issuance_policy.require_refresh_token,
        expiry_threshold_minutes=template.issuance_policy.expiry_threshold_minutes,
    )

    result = vault.update_template(
        template.id,
        actor="alice",
        name=template.name,
        scopes=list(template.scopes),
        kind=template.kind,
        issuance_policy=same_policy,
    )

    assert result.audit_log == template.audit_log


def test_get_template_enforces_scope() -> None:
    cipher = AesGcmCredentialCipher(key="template-scope")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    template = vault.create_template(
        name="Restricted",
        provider="internal",
        scopes=["read"],
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )

    with pytest.raises(WorkflowScopeError):
        vault.get_template(
            template_id=template.id,
            context=CredentialAccessContext(workflow_id=uuid4()),
        )


def test_record_template_issuance_validates_scope() -> None:
    cipher = AesGcmCredentialCipher(key="issuance-scope")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    template = vault.create_template(
        name="Restricted",
        provider="service",
        scopes=["read"],
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )

    with pytest.raises(WorkflowScopeError):
        vault.record_template_issuance(
            template_id=template.id,
            actor="ops",
            credential_id=uuid4(),
            context=CredentialAccessContext(workflow_id=uuid4()),
        )


def test_record_template_issuance_records_audit_event() -> None:
    cipher = AesGcmCredentialCipher(key="issuance-event")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    context = CredentialAccessContext(workflow_id=workflow_id)

    template = vault.create_template(
        name="Service",
        provider="service",
        scopes=["read"],
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )

    credential_id = uuid4()
    updated = vault.record_template_issuance(
        template_id=template.id,
        actor="system",
        credential_id=credential_id,
        context=context,
    )

    assert len(updated.audit_log) == len(template.audit_log) + 1
    assert updated.audit_log[-1].action == "credential_issued"
    assert updated.audit_log[-1].actor == "system"


def test_inmemory_remove_template_missing() -> None:
    vault = InMemoryCredentialVault()
    with pytest.raises(CredentialTemplateNotFoundError):
        vault._remove_template(uuid4())


def test_inmemory_iter_templates_filters_by_workspace() -> None:
    vault = InMemoryCredentialVault()
    vault.create_template(
        name="Shared",
        provider="service",
        scopes=["read"],
        actor="ops",
        workspace_id="workspace-a",
    )
    vault.create_template(
        name="Other",
        provider="service",
        scopes=["read"],
        actor="ops",
        workspace_id="workspace-b",
    )

    templates = list(vault._iter_templates(workspace_id="workspace-a"))

    assert [template.name for template in templates] == ["Shared"]
