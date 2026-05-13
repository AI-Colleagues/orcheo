"""Credential lifecycle tests for FileCredentialVault."""

from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.models import (
    AesGcmCredentialCipher,
    CredentialAccessContext,
    CredentialHealthStatus,
    CredentialKind,
    GovernanceAlertKind,
    OAuthTokenSecrets,
    SecretGovernanceAlertSeverity,
)
from orcheo.vault import (
    CredentialNotFoundError,
    DuplicateCredentialNameError,
    FileCredentialVault,
    GovernanceAlertNotFoundError,
)


def test_vault_accepts_string_kind_and_updates_health(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    cipher = AesGcmCredentialCipher(key="file-key")
    vault_path = tmp_path_factory.mktemp("vault") / "credentials.sqlite"
    vault = FileCredentialVault(path=vault_path, cipher=cipher)

    metadata = vault.create_credential(
        name="GitHub",
        provider="github",
        scopes=["repo"],
        secret="token",
        actor="alice",
        kind="oauth",
    )

    assert metadata.kind is CredentialKind.OAUTH

    metadata = vault.update_oauth_tokens(
        credential_id=metadata.id,
        tokens=OAuthTokenSecrets(access_token="access"),
        actor="alice",
    )
    assert metadata.reveal_oauth_tokens(cipher=cipher) is not None


def test_file_vault_persists_credentials(tmp_path) -> None:
    cipher = AesGcmCredentialCipher(key="file-backend-key")
    vault_path = tmp_path / "vault.sqlite"

    vault = FileCredentialVault(vault_path, cipher=cipher)
    workflow_id = uuid4()
    workflow_context = CredentialAccessContext(workflow_id=workflow_id)
    metadata = vault.create_credential(
        name="Stripe",
        provider="stripe",
        scopes=["payments:write"],
        secret="sk_live_initial",
        actor="alice",
    )

    assert metadata.kind is CredentialKind.SECRET

    restored = FileCredentialVault(vault_path, cipher=cipher)
    assert (
        restored.reveal_secret(credential_id=metadata.id, context=workflow_context)
        == "sk_live_initial"
    )

    restored.rotate_secret(
        credential_id=metadata.id,
        secret="sk_live_rotated",
        actor="security-bot",
        context=workflow_context,
    )

    reloaded = FileCredentialVault(vault_path, cipher=cipher)
    assert (
        reloaded.reveal_secret(credential_id=metadata.id, context=workflow_context)
        == "sk_live_rotated"
    )

    listed = reloaded.list_credentials(context=workflow_context)
    assert len(listed) == 1
    assert listed[0].provider == "stripe"

    masked = reloaded.describe_credentials(context=workflow_context)
    assert masked[0]["provider"] == "stripe"
    assert "ciphertext" not in masked[0]["encryption"]
    assert masked[0]["health"]["status"] == CredentialHealthStatus.UNKNOWN.value

    with pytest.raises(CredentialNotFoundError):
        reloaded.reveal_secret(
            credential_id=uuid4(),
            context=CredentialAccessContext(workflow_id=workflow_id),
        )


def test_file_vault_remove_credential_missing(tmp_path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.sqlite")
    with pytest.raises(CredentialNotFoundError):
        vault._remove_credential(uuid4())


def test_file_vault_rejects_duplicate_names(tmp_path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.sqlite")
    vault.create_credential(
        name="Service",
        provider="service",
        scopes=["read"],
        secret="secret",
        actor="ops",
        workspace_id="workspace-a",
    )

    with pytest.raises(DuplicateCredentialNameError):
        vault.create_credential(
            name="Service",
            provider="service",
            scopes=["write"],
            secret="another",
            actor="ops",
            workspace_id="workspace-a",
        )


def test_file_vault_delete_credential(tmp_path) -> None:
    cipher = AesGcmCredentialCipher(key="file-delete")
    vault = FileCredentialVault(tmp_path / "vault.sqlite", cipher=cipher)

    metadata = vault.create_credential(
        name="Service",
        provider="service",
        scopes=["read"],
        secret="secret",
        actor="ops",
    )

    alert = vault.record_alert(
        kind=GovernanceAlertKind.VALIDATION_FAILED,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="bad",
        actor="ops",
        credential_id=metadata.id,
    )

    vault.delete_credential(metadata.id)

    with pytest.raises(CredentialNotFoundError):
        vault.reveal_secret(credential_id=metadata.id)

    with pytest.raises(GovernanceAlertNotFoundError):
        vault.acknowledge_alert(alert.id, actor="ops")
