"""Tests for `_credential_to_response` helper."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
from orcheo.models import (
    CredentialKind,
    CredentialMetadata,
    CredentialScope,
    EncryptionEnvelope,
)
from orcheo_backend.app import _credential_to_response


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
