"""Tests for credential response helpers."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
from orcheo.models import (
    CredentialKind,
    CredentialMetadata,
    CredentialScope,
    EncryptionEnvelope,
    GovernanceAlertKind,
    SecretGovernanceAlert,
    SecretGovernanceAlertSeverity,
)
from orcheo_backend.app import _credential_to_response
from orcheo_backend.app.credential_utils import alert_to_response


def test_credential_to_response_oauth() -> None:
    """Credential to response converts OAuth metadata correctly."""

    cred_id = uuid4()
    metadata = CredentialMetadata(
        id=cred_id,
        name="Test OAuth Credential",
        provider="slack",
        kind=CredentialKind.OAUTH,
        scope=CredentialScope(),
        encryption=EncryptionEnvelope(
            algorithm="aes-256-gcm",
            key_id="test-key",
            ciphertext="encrypted-data",
        ),
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    response = _credential_to_response(metadata)

    assert response.id == str(cred_id)
    assert response.name == "Test OAuth Credential"
    assert response.provider == "slack"
    assert response.kind == "oauth"
    assert response.secret_preview == "oauth-token"
    assert response.access == "shared"


def test_credential_to_response_secret() -> None:
    """Credential to response converts secret metadata correctly."""

    cred_id = uuid4()
    workflow_id = uuid4()
    metadata = CredentialMetadata(
        id=cred_id,
        name="Test Secret",
        provider="custom",
        kind=CredentialKind.SECRET,
        scope=CredentialScope(workflow_ids=[workflow_id]),
        encryption=EncryptionEnvelope(
            algorithm="aes-256-gcm",
            key_id="test-key",
            ciphertext="encrypted-data",
        ),
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    response = _credential_to_response(metadata)

    assert response.id == str(cred_id)
    assert response.kind == "secret"
    assert response.secret_preview == "••••••••"
    assert response.access == "scoped"
    assert response.workflow_id == str(workflow_id)


def test_credential_to_response_without_owner() -> None:
    """Credential to response handles empty audit log."""

    cred_id = uuid4()
    metadata = CredentialMetadata(
        id=cred_id,
        name="Test Credential",
        provider="slack",
        kind=CredentialKind.OAUTH,
        scope=CredentialScope(),
        encryption=EncryptionEnvelope(
            algorithm="aes-256-gcm",
            key_id="test-key",
            ciphertext="encrypted-data",
        ),
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    response = _credential_to_response(metadata)

    assert response.owner is None


def test_alert_to_response_converts_governance_alert() -> None:
    """alert_to_response converts a SecretGovernanceAlert to a response payload."""
    cred_id = uuid4()
    template_id = uuid4()
    alert = SecretGovernanceAlert.create(
        scope=CredentialScope(),
        kind=GovernanceAlertKind.VALIDATION_FAILED,
        severity=SecretGovernanceAlertSeverity.CRITICAL,
        message="Credential validation failed",
        actor="system",
        credential_id=cred_id,
        template_id=template_id,
    )

    response = alert_to_response(alert)

    assert response.id == str(alert.id)
    assert response.kind == GovernanceAlertKind.VALIDATION_FAILED
    assert response.severity == SecretGovernanceAlertSeverity.CRITICAL
    assert response.message == "Credential validation failed"
    assert response.credential_id == str(cred_id)
    assert response.template_id == str(template_id)
    assert response.is_acknowledged is False
    assert response.acknowledged_at is None


def test_alert_to_response_without_credential_or_template() -> None:
    """alert_to_response handles alerts with no credential_id or template_id."""
    alert = SecretGovernanceAlert.create(
        scope=CredentialScope(),
        kind=GovernanceAlertKind.ROTATION_OVERDUE,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="Rotation is overdue",
        actor="system",
    )

    response = alert_to_response(alert)

    assert response.credential_id is None
    assert response.template_id is None
