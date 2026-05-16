"""Credential metadata and scope tests split from the original suite."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4
import pytest
from orcheo.models import (
    AesGcmCredentialCipher,
    CredentialAccessContext,
    CredentialCipher,
    CredentialHealthStatus,
    CredentialKind,
    CredentialMetadata,
    CredentialScope,
    CredentialTemplate,
    EncryptionEnvelope,
    OAuthTokenSecrets,
)
from orcheo.models import OAuthTokenPayload


def test_credential_template_scope_normalization_handles_duplicates() -> None:
    template = CredentialTemplate.create(
        name="API",
        provider="service",
        scopes=["read", "read", "  write  ", ""],
        actor="alice",
    )

    assert template.scopes == ["read", "write"]


def test_credential_metadata_encrypts_and_redacts_secrets() -> None:
    cipher = AesGcmCredentialCipher(key="super-secret-key", key_id="k1")

    metadata = CredentialMetadata.create(
        name="OpenAI",
        provider="openai",
        scopes=["chat:write", "chat:write"],
        secret="initial-token",
        cipher=cipher,
        actor="alice",
    )

    assert metadata.reveal(cipher=cipher) == "initial-token"
    assert metadata.scopes == ["chat:write"]
    assert metadata.last_rotated_at is not None
    assert metadata.audit_log[-1].action == "credential_created"
    assert metadata.kind is CredentialKind.SECRET
    assert metadata.health.status is CredentialHealthStatus.UNKNOWN

    metadata.rotate_secret(secret="rotated-token", cipher=cipher, actor="bob")
    assert metadata.reveal(cipher=cipher) == "rotated-token"
    assert metadata.audit_log[-1].action == "credential_rotated"
    assert metadata.health.status is CredentialHealthStatus.UNKNOWN

    redacted = metadata.redact()
    assert "ciphertext" not in redacted["encryption"]
    assert redacted["encryption"]["algorithm"] == cipher.algorithm
    assert redacted["encryption"]["key_id"] == cipher.key_id
    assert redacted["scope"] == {
        "workflow_ids": [],
        "workspace_ids": [],
        "roles": [],
    }
    assert redacted["kind"] == "secret"
    assert redacted["oauth_tokens"] is None
    assert redacted["health"]["status"] == CredentialHealthStatus.UNKNOWN.value

    wrong_cipher = AesGcmCredentialCipher(key="other-key", key_id="k1")
    with pytest.raises(ValueError):
        metadata.reveal(cipher=wrong_cipher)

    mismatched_cipher = AesGcmCredentialCipher(key="super-secret-key", key_id="k2")
    with pytest.raises(ValueError):
        metadata.reveal(cipher=mismatched_cipher)

    class OtherCipher(Protocol):
        algorithm: str
        key_id: str

        def decrypt(self, envelope: EncryptionEnvelope) -> str: ...

    class DummyCipher:
        algorithm = "other"
        key_id = cipher.key_id

        def encrypt(self, plaintext: str) -> EncryptionEnvelope:
            raise NotImplementedError

        def decrypt(self, envelope: EncryptionEnvelope) -> str:
            return ""

    dummy_cipher: CredentialCipher = DummyCipher()
    with pytest.raises(ValueError):
        metadata.encryption.decrypt(dummy_cipher)


def test_credential_metadata_oauth_token_management() -> None:
    cipher = AesGcmCredentialCipher(key="oauth-secret")
    expiry = datetime.now(tz=UTC) + timedelta(hours=1)
    metadata = CredentialMetadata.create(
        name="Slack",
        provider="slack",
        scopes=["chat:write"],
        secret="client-secret",
        cipher=cipher,
        actor="alice",
        kind=CredentialKind.OAUTH,
        oauth_tokens=OAuthTokenSecrets(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at=expiry,
        ),
    )

    tokens = metadata.reveal_oauth_tokens(cipher=cipher)
    assert tokens is not None
    assert tokens.access_token == "access-1"
    assert tokens.refresh_token == "refresh-1"
    assert tokens.expires_at == expiry

    metadata.update_oauth_tokens(
        cipher=cipher,
        tokens=OAuthTokenSecrets(access_token="access-2", expires_at=None),
        actor="validator",
    )
    rotated_tokens = metadata.reveal_oauth_tokens(cipher=cipher)
    assert rotated_tokens is not None
    assert rotated_tokens.access_token == "access-2"
    assert rotated_tokens.refresh_token is None
    assert rotated_tokens.expires_at is None

    metadata.mark_health(
        status=CredentialHealthStatus.HEALTHY,
        reason=None,
        actor="validator",
    )
    assert metadata.health.status is CredentialHealthStatus.HEALTHY
    assert metadata.health.failure_reason is None
    assert metadata.health.last_checked_at is not None

    redacted = metadata.redact()
    assert redacted["kind"] == "oauth"
    assert redacted["oauth_tokens"]["has_access_token"] is True
    assert redacted["oauth_tokens"]["has_refresh_token"] is False
    assert redacted["health"]["status"] == CredentialHealthStatus.HEALTHY.value

    metadata.health.update(status=CredentialHealthStatus.HEALTHY)
    metadata.rotate_secret(secret="new-secret", cipher=cipher, actor="ops")
    assert metadata.health.status is CredentialHealthStatus.UNKNOWN

    metadata.update_oauth_tokens(cipher=cipher, tokens=None, actor="ops")
    assert metadata.reveal_oauth_tokens(cipher=cipher) is None


def test_oauth_token_models_normalize_naive_expiry() -> None:
    cipher = AesGcmCredentialCipher(key="normalize")
    naive = datetime(2025, 1, 1, 12, 0, 0)

    secrets = OAuthTokenSecrets(expires_at=naive)
    assert secrets.expires_at is not None
    assert secrets.expires_at.tzinfo is UTC

    payload = OAuthTokenPayload.from_secrets(cipher=cipher, secrets=secrets)
    assert payload.expires_at is not None
    assert payload.expires_at.tzinfo is UTC

    empty_payload = OAuthTokenPayload.from_secrets(cipher=cipher, secrets=None)
    assert empty_payload.access_token is None
    assert empty_payload.refresh_token is None
    assert empty_payload.expires_at is None

    direct_payload = OAuthTokenPayload(expires_at=naive)
    assert direct_payload.expires_at is not None
    assert direct_payload.expires_at.tzinfo is UTC


def test_update_oauth_tokens_rejects_non_oauth_credentials() -> None:
    cipher = AesGcmCredentialCipher(key="non-oauth")
    metadata = CredentialMetadata.create(
        name="Webhook Secret",
        provider="internal",
        scopes=[],
        secret="secret",
        cipher=cipher,
        actor="ops",
        kind=CredentialKind.SECRET,
    )

    with pytest.raises(ValueError):
        metadata.update_oauth_tokens(cipher=cipher, tokens=None)


def test_credential_scope_allows_multiple_constraints() -> None:
    workflow_id = uuid4()
    workspace_id = uuid4()

    unrestricted = CredentialScope.unrestricted()
    assert unrestricted.is_unrestricted()
    assert unrestricted.allows(CredentialAccessContext())

    workflow_scope = CredentialScope.for_workflows(workflow_id, workflow_id)
    assert workflow_scope.allows(CredentialAccessContext(workflow_id=workflow_id))
    assert not workflow_scope.allows(CredentialAccessContext(workflow_id=uuid4()))

    workspace_scope = CredentialScope.for_workspaces(workspace_id)
    assert workspace_scope.allows(CredentialAccessContext(workspace_id=workspace_id))
    assert not workspace_scope.allows(CredentialAccessContext())
    assert workspace_scope.scope_hint() == str(workspace_id)

    combined = CredentialScope(
        workflow_ids=[workflow_id],
        workspace_ids=[workspace_id],
        roles=["Admin", "admin"],
    )
    context = CredentialAccessContext(
        workflow_id=workflow_id,
        workspace_id=workspace_id,
        roles=["operator", "Admin"],
    )
    assert combined.allows(context)
    assert combined.scope_hint() == str(workflow_id)
    assert not combined.is_unrestricted()

    mismatched_roles = CredentialAccessContext(
        workflow_id=workflow_id,
        workspace_id=workspace_id,
        roles=["viewer"],
    )
    assert not combined.allows(mismatched_roles)

    role_only_scope = CredentialScope.for_roles("Admin", "Admin", " ")
    assert role_only_scope.scope_hint() == "admin"
    assert role_only_scope.roles == ["admin"]
    assert not role_only_scope.allows(CredentialAccessContext())

    normalized_context = CredentialAccessContext(roles=["Admin", "admin", " "])
    assert normalized_context.roles == ["admin"]
