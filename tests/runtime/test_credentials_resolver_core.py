"""Core CredentialResolver behavior tests."""

from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.models import (
    CredentialAccessContext,
    CredentialKind,
    CredentialScope,
    OAuthTokenSecrets,
)
from orcheo.runtime.credentials import (
    CredentialReference,
    CredentialReferenceNotFoundError,
    CredentialResolver,
    DuplicateCredentialReferenceError,
    UnknownCredentialPayloadError,
    credential_ref,
    credential_resolution,
    get_active_credential_resolver,
)
from orcheo.vault import DuplicateCredentialNameError, InMemoryCredentialVault
from tests.runtime.credential_test_helpers import create_vault_with_secret


def test_resolver_caches_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    vault = create_vault_with_secret()
    resolver = CredentialResolver(vault)
    reference = CredentialReference(identifier="telegram_token")

    reveal_calls: list[int] = []
    original_reveal = vault.reveal_secret

    def _wrapped(**kwargs):  # type: ignore[no-untyped-def]
        reveal_calls.append(1)
        return original_reveal(**kwargs)

    monkeypatch.setattr(vault, "reveal_secret", _wrapped)

    with credential_resolution(resolver):
        assert resolver.resolve(reference) == "s3cret"
        assert resolver.resolve(reference) == "s3cret"

    assert len(reveal_calls) == 1


def test_resolver_rejects_unknown_payload() -> None:
    vault = create_vault_with_secret()
    resolver = CredentialResolver(vault)
    with credential_resolution(resolver):
        with pytest.raises(UnknownCredentialPayloadError):
            resolver.resolve(credential_ref("telegram_token", "oauth"))
        with pytest.raises(UnknownCredentialPayloadError):
            resolver.resolve(credential_ref("telegram_token", "secret.value"))
        with pytest.raises(UnknownCredentialPayloadError):
            resolver.resolve(credential_ref("telegram_token", "api_key"))


def test_resolver_rejects_duplicate_name_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = create_vault_with_secret()
    resolver = CredentialResolver(vault)
    metadata = vault.list_credentials(context=None)[0]
    duplicate = metadata.model_copy(update={"id": uuid4()}, deep=True)
    metadata_by_id = {metadata.id: metadata, duplicate.id: duplicate}
    metadata_by_name = {metadata.name: [metadata, duplicate]}

    monkeypatch.setattr(
        resolver,
        "_load_metadata_index",
        lambda: (metadata_by_id, metadata_by_name),
    )

    with pytest.raises(DuplicateCredentialReferenceError):
        resolver.resolve(credential_ref(metadata.name))


def test_resolver_scopes_resolution_to_its_workspace() -> None:
    """A resolver only resolves credentials in its bound workspace.

    Security guarantee for restricted (untrusted, multi-tenant) mode: a
    workflow author who writes ``[[name]]`` in built-in node config can only
    reach credentials in the workflow's own workspace, never another tenant's.
    """
    workspace_a = uuid4()
    workspace_b = uuid4()
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name="ApiKey",
        provider="svc",
        scopes=["read"],
        secret="workspace-a-secret",
        actor="ops",
        workspace_id=str(workspace_a),
    )
    vault.create_credential(
        name="OtherTenantOnly",
        provider="svc",
        scopes=["read"],
        secret="workspace-b-secret",
        actor="ops",
        workspace_id=str(workspace_b),
    )

    resolver_a = CredentialResolver(
        vault, context=CredentialAccessContext(workspace_id=workspace_a)
    )
    assert resolver_a.resolve(credential_ref("ApiKey")) == "workspace-a-secret"

    # A credential that exists only in workspace B is invisible to workspace A.
    with pytest.raises(CredentialReferenceNotFoundError):
        resolver_a.resolve(credential_ref("OtherTenantOnly"))


def test_resolver_does_not_leak_same_named_credential_across_workspaces() -> None:
    """Same-named credentials in different workspaces stay isolated per resolver."""
    workspace_a = uuid4()
    workspace_b = uuid4()
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name="ApiKey",
        provider="svc",
        scopes=["read"],
        secret="workspace-a-secret",
        actor="ops",
        workspace_id=str(workspace_a),
    )
    vault.create_credential(
        name="ApiKey",
        provider="svc",
        scopes=["read"],
        secret="workspace-b-secret",
        actor="ops",
        workspace_id=str(workspace_b),
    )

    resolver_a = CredentialResolver(
        vault, context=CredentialAccessContext(workspace_id=workspace_a)
    )
    resolver_b = CredentialResolver(
        vault, context=CredentialAccessContext(workspace_id=workspace_b)
    )
    assert resolver_a.resolve(credential_ref("ApiKey")) == "workspace-a-secret"
    assert resolver_b.resolve(credential_ref("ApiKey")) == "workspace-b-secret"


def test_vault_rejects_duplicate_names() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name="dup",
        provider="telegram",
        scopes=["bot"],
        secret="value",
        actor="tester",
        scope=CredentialScope.unrestricted(),
    )
    with pytest.raises(DuplicateCredentialNameError):
        vault.create_credential(
            name="dup",
            provider="telegram",
            scopes=["bot"],
            secret="another",
            actor="tester",
            scope=CredentialScope.unrestricted(),
        )


def test_resolver_missing_reference() -> None:
    vault = create_vault_with_secret()
    resolver = CredentialResolver(vault)
    with credential_resolution(resolver):
        with pytest.raises(CredentialReferenceNotFoundError):
            resolver.resolve(credential_ref("missing"))


def test_get_active_resolver_returns_current_instance() -> None:
    vault = create_vault_with_secret()
    resolver = CredentialResolver(vault)
    assert get_active_credential_resolver() is None
    with credential_resolution(resolver):
        assert get_active_credential_resolver() is resolver
    assert get_active_credential_resolver() is None


def test_credential_resolution_allows_null_resolver() -> None:
    with credential_resolution(None) as active:
        assert active is None
        assert get_active_credential_resolver() is None


def test_resolver_respects_context_scope() -> None:
    vault = InMemoryCredentialVault()
    allowed = uuid4()
    denied = uuid4()
    vault.create_credential(
        name="scoped",
        provider="telegram",
        scopes=["bot"],
        secret="token",
        actor="tester",
        scope=CredentialScope.for_workflows(allowed),
    )
    resolver = CredentialResolver(
        vault, context=CredentialAccessContext(workflow_id=allowed)
    )
    with credential_resolution(resolver):
        assert resolver.resolve(credential_ref("scoped")) == "token"

    restricted_resolver = CredentialResolver(
        vault, context=CredentialAccessContext(workflow_id=denied)
    )
    with credential_resolution(restricted_resolver):
        with pytest.raises(CredentialReferenceNotFoundError):
            restricted_resolver.resolve(credential_ref("scoped"))


def test_resolver_allows_uuid_identifiers() -> None:
    vault = create_vault_with_secret(secret="token")
    metadata = vault.list_credentials()[0]
    resolver = CredentialResolver(vault)
    with credential_resolution(resolver):
        value = resolver.resolve(CredentialReference(identifier=str(metadata.id)))
        assert value == "token"


def test_resolver_handles_uuid_like_name() -> None:
    uuid_name = str(uuid4())
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name=uuid_name,
        provider="telegram",
        scopes=["bot"],
        secret="token",
        actor="tester",
        scope=CredentialScope.unrestricted(),
    )
    resolver = CredentialResolver(vault)
    with credential_resolution(resolver):
        assert resolver.resolve(credential_ref(uuid_name)) == "token"


def test_resolver_accepts_explicit_empty_payload_path() -> None:
    vault = create_vault_with_secret(secret="token")
    resolver = CredentialResolver(vault)
    with credential_resolution(resolver):
        value = resolver.resolve(CredentialReference("telegram_token", ()))
        assert value == "token"


def test_persist_refreshed_tokens_passes_context_for_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name="oauth_bot",
        provider="oauth",
        scopes=["bot"],
        secret="ignored",
        actor="tester",
        kind=CredentialKind.OAUTH,
        scope=CredentialScope.unrestricted(),
        oauth_tokens=OAuthTokenSecrets(access_token="old-access"),
    )
    context = CredentialAccessContext(workspace_id=uuid4())
    resolver = CredentialResolver(vault, context=context)

    captured: dict[str, object] = {}

    def _capture_update_oauth_tokens(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(vault, "update_oauth_tokens", _capture_update_oauth_tokens)

    updated = resolver.persist_refreshed_tokens(
        "oauth_bot",
        new_access_token="new-access",
        new_refresh_token="new-refresh",
    )

    assert updated is True
    assert captured["context"] is context


def test_persist_refreshed_tokens_returns_false_when_not_found() -> None:
    """persist_refreshed_tokens returns False when the credential name does not exist."""  # noqa: E501
    vault = InMemoryCredentialVault()
    resolver = CredentialResolver(vault)
    result = resolver.persist_refreshed_tokens(
        "nonexistent_credential", new_access_token="new-token"
    )
    assert result is False


def test_persist_refreshed_tokens_returns_false_for_duplicate_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """persist_refreshed_tokens returns False when the name is ambiguous (duplicate)."""
    from uuid import uuid4
    from orcheo.models import CredentialScope

    vault = InMemoryCredentialVault()
    metadata = vault.create_credential(
        name="ambiguous_token",
        provider="oauth",
        scopes=["bot"],
        secret="ignored",
        actor="tester",
        scope=CredentialScope.unrestricted(),
    )
    duplicate = metadata.model_copy(update={"id": uuid4()}, deep=True)
    metadata_by_id = {metadata.id: metadata, duplicate.id: duplicate}
    metadata_by_name = {"ambiguous_token": [metadata, duplicate]}

    resolver = CredentialResolver(vault)
    monkeypatch.setattr(
        resolver,
        "_load_metadata_index",
        lambda: (metadata_by_id, metadata_by_name),
    )
    result = resolver.persist_refreshed_tokens(
        "ambiguous_token", new_access_token="new-token"
    )
    assert result is False


def test_persist_refreshed_tokens_passes_context_for_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name="secret_bot",
        provider="telegram",
        scopes=["bot"],
        secret="old-secret",
        actor="tester",
        scope=CredentialScope.unrestricted(),
    )
    context = CredentialAccessContext(workflow_id=uuid4())
    resolver = CredentialResolver(vault, context=context)

    captured: dict[str, object] = {}

    def _capture_update_credential(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(vault, "update_credential", _capture_update_credential)

    updated = resolver.persist_refreshed_tokens(
        "secret_bot",
        new_access_token="new-secret",
    )

    assert updated is True
    assert captured["context"] is context
