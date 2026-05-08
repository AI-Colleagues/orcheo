"""Tests for credential operations backed by InMemoryCredentialVault."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
from orcheo.models import (
    AesGcmCredentialCipher,
    CredentialAccessContext,
    CredentialHealthStatus,
    CredentialKind,
    CredentialScope,
    GovernanceAlertKind,
    OAuthTokenSecrets,
    SecretGovernanceAlertSeverity,
)
from orcheo.vault import (
    CredentialNotFoundError,
    DuplicateCredentialNameError,
    InMemoryCredentialVault,
)
from orcheo.vault.errors import RotationPolicyError, WorkflowScopeError


def test_vault_updates_oauth_tokens_and_health() -> None:
    cipher = AesGcmCredentialCipher(key="oauth-test-key")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    context = CredentialAccessContext(workflow_id=workflow_id)
    expiry = datetime.now(tz=UTC) + timedelta(minutes=30)

    metadata = vault.create_credential(
        name="Slack",
        provider="slack",
        scopes=["chat:write"],
        secret="client-secret",
        actor="alice",
        scope=CredentialScope.for_workflows(workflow_id),
        kind=CredentialKind.OAUTH,
        oauth_tokens=OAuthTokenSecrets(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at=expiry,
        ),
    )

    tokens = metadata.reveal_oauth_tokens(cipher=cipher)
    assert tokens is not None and tokens.refresh_token == "refresh-1"

    updated = vault.update_oauth_tokens(
        credential_id=metadata.id,
        tokens=OAuthTokenSecrets(access_token="access-2"),
        actor="validator",
        context=context,
    )
    rotated_tokens = updated.reveal_oauth_tokens(cipher=cipher)
    assert rotated_tokens is not None
    assert rotated_tokens.access_token == "access-2"
    assert rotated_tokens.refresh_token is None
    assert rotated_tokens.expires_at is None

    healthy = vault.mark_health(
        credential_id=metadata.id,
        status=CredentialHealthStatus.HEALTHY,
        reason=None,
        actor="validator",
        context=context,
    )
    assert healthy.health.status is CredentialHealthStatus.HEALTHY

    masked = vault.describe_credentials(context=context)[0]
    assert masked["oauth_tokens"]["has_access_token"] is True
    assert masked["oauth_tokens"]["has_refresh_token"] is False
    assert masked["health"]["status"] == CredentialHealthStatus.HEALTHY.value


def test_vault_cipher_property_access() -> None:
    cipher = AesGcmCredentialCipher(key="cipher-property-test")
    vault = InMemoryCredentialVault(cipher=cipher)
    assert vault.cipher is cipher
    assert vault.cipher.algorithm == "aes256-gcm.v1"


def test_delete_credential_removes_credential_and_alerts() -> None:
    cipher = AesGcmCredentialCipher(key="delete-credential")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    context = CredentialAccessContext(workflow_id=workflow_id)

    metadata = vault.create_credential(
        name="Service",
        provider="service",
        scopes=["read"],
        secret="secret",
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )

    vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="expiring",
        actor="ops",
        credential_id=metadata.id,
        context=context,
    )
    other = vault.create_credential(
        name="Other",
        provider="service",
        scopes=["read"],
        secret="secret-2",
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )
    vault.record_alert(
        kind=GovernanceAlertKind.TOKEN_EXPIRING,
        severity=SecretGovernanceAlertSeverity.WARNING,
        message="other",
        actor="ops",
        credential_id=other.id,
        context=context,
    )

    assert len(vault.list_credentials(context=context)) == 2
    assert len(vault.list_alerts(context=context)) == 2

    vault.delete_credential(metadata.id, context=context)

    assert len(vault.list_credentials(context=context)) == 1
    assert len(vault.list_alerts(context=context)) == 1


def test_inmemory_remove_credential_missing() -> None:
    vault = InMemoryCredentialVault()
    with pytest.raises(CredentialNotFoundError):
        vault._remove_credential(uuid4())


def test_update_credential_updates_scope_without_secret_rotation() -> None:
    cipher = AesGcmCredentialCipher(key="update-credential-scope")
    vault = InMemoryCredentialVault(cipher=cipher)
    workflow_id = uuid4()
    context = CredentialAccessContext(workflow_id=workflow_id)

    metadata = vault.create_credential(
        name="Service",
        provider="svc",
        scopes=["read"],
        secret="secret-value",
        actor="ops",
        scope=CredentialScope.unrestricted(),
    )

    updated = vault.update_credential(
        credential_id=metadata.id,
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
        context=context,
    )

    assert updated.scope == CredentialScope.for_workflows(workflow_id)
    assert len(updated.audit_log) == len(metadata.audit_log) + 1
    assert updated.audit_log[-1].action == "credential_updated"
    assert (
        vault.reveal_secret(credential_id=metadata.id, context=context)
        == "secret-value"
    )


def test_update_credential_rejects_empty_provider() -> None:
    vault = InMemoryCredentialVault()
    metadata = vault.create_credential(
        name="Service",
        provider="svc",
        scopes=["read"],
        secret="secret-value",
        actor="ops",
    )

    with pytest.raises(ValueError, match="provider cannot be empty"):
        vault.update_credential(credential_id=metadata.id, actor="ops", provider="  ")


def test_update_credential_tracks_multiple_field_changes() -> None:
    cipher = AesGcmCredentialCipher(key="update-credential")
    vault = InMemoryCredentialVault(cipher=cipher)
    workspace_id = uuid4()
    metadata = vault.create_credential(
        name="Service",
        provider="svc",
        scopes=["read"],
        secret="secret-value",
        actor="ops",
    )

    updated = vault.update_credential(
        credential_id=metadata.id,
        actor="ops",
        name="Renamed",
        provider="svc-new",
        secret="rotated-secret",
        scope=CredentialScope.for_workspaces(workspace_id),
    )

    assert updated.name == "Renamed"
    assert updated.provider == "svc-new"
    assert updated.reveal(cipher=cipher) == "rotated-secret"
    assert updated.audit_log[-1].action == "credential_updated"

    rotated_again = vault.update_credential(
        credential_id=metadata.id,
        actor="ops",
        secret="rotated-again",
        context=CredentialAccessContext(workspace_id=workspace_id),
    )

    assert rotated_again.reveal(cipher=cipher) == "rotated-again"


def test_rotate_secret_rejects_identical_secret() -> None:
    cipher = AesGcmCredentialCipher(key="rotate-secret")
    vault = InMemoryCredentialVault(cipher=cipher)
    metadata = vault.create_credential(
        name="Service",
        provider="svc",
        scopes=["read"],
        secret="secret-value",
        actor="ops",
    )

    with pytest.raises(
        RotationPolicyError, match="must differ from the previous value"
    ):
        vault.rotate_secret(
            credential_id=metadata.id, secret="secret-value", actor="ops"
        )


def test_get_metadata_rejects_workspace_mismatch() -> None:
    vault = InMemoryCredentialVault()
    metadata = vault.create_credential(
        name="Service",
        provider="svc",
        scopes=["read"],
        secret="secret-value",
        actor="ops",
        workspace_id="workspace-a",
    )

    with pytest.raises(WorkflowScopeError, match="provided context"):
        vault.reveal_secret(
            credential_id=metadata.id,
            context=CredentialAccessContext(workspace_id=uuid4()),
        )


def test_get_metadata_rejects_scope_mismatch() -> None:
    workflow_id = uuid4()
    vault = InMemoryCredentialVault()
    metadata = vault.create_credential(
        name="Service",
        provider="svc",
        scopes=["read"],
        secret="secret-value",
        actor="ops",
        scope=CredentialScope.for_workflows(workflow_id),
    )

    with pytest.raises(WorkflowScopeError, match="provided context"):
        vault.reveal_secret(
            credential_id=metadata.id,
            context=CredentialAccessContext(workflow_id=uuid4()),
        )


def test_list_all_credentials_filters_workspace() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name="Global",
        provider="svc",
        scopes=["read"],
        secret="global-secret",
        actor="ops",
    )
    vault.create_credential(
        name="Workspace A",
        provider="svc",
        scopes=["read"],
        secret="workspace-a-secret",
        actor="ops",
        workspace_id="workspace-a",
    )
    vault.create_credential(
        name="Workspace B",
        provider="svc",
        scopes=["read"],
        secret="workspace-b-secret",
        actor="ops",
        workspace_id="workspace-b",
    )

    listed = vault.list_all_credentials(workspace_id="workspace-a")

    assert [item.name for item in listed] == ["Global", "Workspace A"]


def test_inmemory_rejects_duplicate_names_per_workspace() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name="Service",
        provider="svc",
        scopes=["read"],
        secret="secret-1",
        actor="ops",
        workspace_id="workspace-a",
    )

    with pytest.raises(DuplicateCredentialNameError):
        vault.create_credential(
            name="Service",
            provider="svc",
            scopes=["read"],
            secret="secret-2",
            actor="ops",
            workspace_id="workspace-a",
        )


def test_inmemory_allows_duplicate_names_across_workspaces() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name="Service",
        provider="svc",
        scopes=["read"],
        secret="secret-1",
        actor="ops",
        workspace_id="workspace-a",
    )
    vault.create_credential(
        name="Service",
        provider="svc",
        scopes=["read"],
        secret="secret-2",
        actor="ops",
        workspace_id="workspace-b",
    )

    assert len(vault.list_all_credentials()) == 2


def test_inmemory_load_missing_credential_raises() -> None:
    vault = InMemoryCredentialVault()

    with pytest.raises(CredentialNotFoundError):
        vault._load_metadata(uuid4())
