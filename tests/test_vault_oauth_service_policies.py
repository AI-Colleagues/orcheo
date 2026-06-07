"""OAuth credential service policy and configuration tests."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
from orcheo.models import (
    AesGcmCredentialCipher,
    CredentialAccessContext,
    CredentialIssuancePolicy,
    CredentialKind,
    CredentialScope,
    GovernanceAlertKind,
    OAuthTokenSecrets,
)
from orcheo.vault import InMemoryCredentialVault
from orcheo.vault.oauth import OAuthCredentialService


def test_oauth_service_validates_configuration() -> None:
    vault = InMemoryCredentialVault()
    with pytest.raises(ValueError):
        OAuthCredentialService(vault, token_ttl_seconds=0)

    service = OAuthCredentialService(vault, token_ttl_seconds=60)
    with pytest.raises(ValueError):
        service.register_provider("", object())  # type: ignore[arg-type]


def test_oauth_service_register_provider_stores_handler() -> None:
    """register_provider with a valid name stores the handler in _providers."""
    from orcheo.vault.oauth.models import OAuthProvider

    class _NoopProvider(OAuthProvider):
        async def refresh_tokens(self, credential):  # pragma: no cover
            return credential

    vault = InMemoryCredentialVault()
    service = OAuthCredentialService(vault, token_ttl_seconds=60)
    handler = _NoopProvider()
    service.register_provider("slack", handler)
    assert service._providers["slack"] is handler


def test_oauth_service_get_report_returns_stored_report() -> None:
    """get_report returns the cached CredentialHealthReport for a workflow."""
    from datetime import UTC, datetime
    from orcheo.vault.oauth.models import CredentialHealthReport

    vault = InMemoryCredentialVault()
    service = OAuthCredentialService(vault, token_ttl_seconds=60)
    workflow_id = uuid4()
    report = CredentialHealthReport(
        workflow_id=workflow_id,
        results=[],
        checked_at=datetime.now(tz=UTC),
    )
    service._reports[workflow_id] = report
    assert service.get_report(workflow_id) is report
    assert service.get_report(uuid4()) is None


def test_oauth_service_refresh_margin_logic() -> None:
    cipher = AesGcmCredentialCipher(key="refresh-logic")
    vault = InMemoryCredentialVault(cipher=cipher)
    service = OAuthCredentialService(vault, token_ttl_seconds=300)

    assert service._should_refresh(None)
    tokens_without_expiry = OAuthTokenSecrets(access_token="a")
    assert not service._should_refresh(tokens_without_expiry)
    expiring_tokens = OAuthTokenSecrets(
        access_token="a",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=2),
    )
    assert service._should_refresh(expiring_tokens)


def test_oauth_service_loads_template_for_metadata() -> None:
    cipher = AesGcmCredentialCipher(key="template-load")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    template = vault.create_template(
        name="Slack",
        provider="slack",
        scopes=["chat:write"],
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
        kind=CredentialKind.OAUTH,
    )
    metadata = vault.create_credential(
        name="Slack",  # type: ignore[arg-type]
        provider="slack",
        scopes=["chat:write"],
        secret="secret",
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
        kind=CredentialKind.OAUTH,
        template_id=template.id,
    )

    service = OAuthCredentialService(vault, token_ttl_seconds=600)
    context = CredentialAccessContext(workflow_id=workflow_id)
    loaded = service._load_template_for_metadata(metadata, context)

    assert loaded is not None and loaded.id == template.id


def test_oauth_service_rotation_policy_triggers_alert(monkeypatch) -> None:
    cipher = AesGcmCredentialCipher(key="rotation-policy")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    template = vault.create_template(
        name="Slack",
        provider="slack",
        scopes=["chat:write"],
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
        kind=CredentialKind.OAUTH,
        issuance_policy=CredentialIssuancePolicy(rotation_period_days=30),
    )
    metadata = vault.create_credential(
        name="Slack",
        provider="slack",
        scopes=["chat:write"],
        secret="secret",
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
        kind=CredentialKind.OAUTH,
        template_id=template.id,
    )
    metadata.last_rotated_at = datetime.now(tz=UTC) - timedelta(days=60)
    vault._persist_metadata(metadata)

    alerts_triggered: set[GovernanceAlertKind] = set()
    recorded: list[GovernanceAlertKind] = []
    original_record_alert = vault.record_alert

    def capture_alert(**kwargs):
        recorded.append(kwargs["kind"])
        return original_record_alert(**kwargs)

    monkeypatch.setattr(vault, "record_alert", capture_alert)

    service = OAuthCredentialService(vault, token_ttl_seconds=600)
    service._apply_rotation_policy(
        template,
        metadata,
        alerts_triggered,
        context=CredentialAccessContext(workflow_id=workflow_id),
        actor_name="ops",
    )

    assert GovernanceAlertKind.ROTATION_OVERDUE in alerts_triggered
    assert recorded == [GovernanceAlertKind.ROTATION_OVERDUE]


def test_oauth_service_validates_template_policy_tokens() -> None:
    vault = InMemoryCredentialVault()
    service = OAuthCredentialService(vault, token_ttl_seconds=120)
    policy = CredentialIssuancePolicy(require_refresh_token=True)

    with pytest.raises(ValueError):
        service._validate_template_policy(policy, oauth_tokens=None)


def test_oauth_service_validate_policy_with_refresh_token() -> None:
    vault = InMemoryCredentialVault()
    service = OAuthCredentialService(vault, token_ttl_seconds=120)
    policy = CredentialIssuancePolicy(require_refresh_token=True)
    tokens = OAuthTokenSecrets(access_token="a", refresh_token="b")

    service._validate_template_policy(policy, oauth_tokens=tokens)
