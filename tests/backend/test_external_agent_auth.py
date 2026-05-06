from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import MagicMock, call
from uuid import uuid4
from orcheo.models import CredentialScope
from orcheo_backend.app.external_agent_auth import (
    CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME,
    CODEX_AUTH_JSON_CREDENTIAL_NAME,
    CODEX_AUTH_JSON_ENV_VAR,
    GEMINI_AUTH_JSON_CREDENTIAL_NAME,
    GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME,
    GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME,
    GEMINI_STATE_JSON_CREDENTIAL_NAME,
    delete_external_agent_secret,
    load_external_agent_vault_environment,
    reveal_external_agent_secret,
    upsert_external_agent_secret,
)


def test_upsert_external_agent_secret_updates_first_match_and_deletes_duplicates() -> (
    None
):
    vault = MagicMock()
    workspace_uuid = uuid4()
    workspace_id = str(workspace_uuid)
    first = SimpleNamespace(
        id=uuid4(), name="CODEX_AUTH_JSON", workspace_id=workspace_id
    )
    duplicate = SimpleNamespace(
        id=uuid4(), name="CODEX_AUTH_JSON", workspace_id=workspace_id
    )
    vault.list_all_credentials.return_value = [first, duplicate]

    upsert_external_agent_secret(
        vault,
        credential_name="CODEX_AUTH_JSON",
        provider="codex",
        secret="{}",
        workspace_id=workspace_id,
    )

    vault.update_credential.assert_called_once_with(
        credential_id=first.id,
        actor="external_agent_worker",
        provider="codex",
        secret="{}",
        scope=CredentialScope.for_workspaces(workspace_uuid),
    )
    vault.delete_credential.assert_called_once_with(duplicate.id)


def test_load_external_agent_vault_environment_materializes_all_secrets() -> None:
    vault = MagicMock()
    workspace_uuid = uuid4()
    workspace_id = str(workspace_uuid)
    claude = SimpleNamespace(
        id=uuid4(),
        name=CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    codex = SimpleNamespace(
        id=uuid4(),
        name=CODEX_AUTH_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    gemini_auth = SimpleNamespace(
        id=uuid4(),
        name=GEMINI_AUTH_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    gemini_accounts = SimpleNamespace(
        id=uuid4(),
        name=GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    gemini_state = SimpleNamespace(
        id=uuid4(),
        name=GEMINI_STATE_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    gemini_oauth = SimpleNamespace(
        id=uuid4(),
        name=GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    vault.list_all_credentials.return_value = [
        claude,
        codex,
        gemini_auth,
        gemini_accounts,
        gemini_state,
        gemini_oauth,
    ]

    def reveal_secret(*, credential_id: object) -> str | None:
        if credential_id == claude.id:
            return "claude-token"
        if credential_id == codex.id:
            return '{"auth": "json"}'
        if credential_id == gemini_auth.id:
            return '{"version":1,"files":{"trustedFolders.json":"{\\"trustedFolders\\":[]}"}}'  # noqa: E501
        if credential_id == gemini_accounts.id:
            return '{"accounts": []}'
        if credential_id == gemini_state.id:
            return '{"state": {}}'
        if credential_id == gemini_oauth.id:
            return '{"oauth": {}}'
        raise AssertionError("unexpected credential id")

    vault.reveal_secret.side_effect = reveal_secret

    environ = load_external_agent_vault_environment(
        vault,
        workspace_id=workspace_id,
    )

    assert vault.list_all_credentials.call_count == 6
    vault.list_all_credentials.assert_has_calls([call(workspace_id=workspace_id)] * 6)

    assert environ == {
        "CLAUDE_CODE_OAUTH_TOKEN": "claude-token",
        CODEX_AUTH_JSON_ENV_VAR: '{"auth": "json"}',
        "GEMINI_AUTH_JSON": (
            '{"version":1,"files":{"trustedFolders.json":"{\\"trustedFolders\\":[]}"}}'
        ),
        "GEMINI_GOOGLE_ACCOUNTS_JSON": '{"accounts": []}',
        "GEMINI_STATE_JSON": '{"state": {}}',
        "GEMINI_OAUTH_CREDS_JSON": '{"oauth": {}}',
    }


def test_reveal_external_agent_secret_returns_none_when_missing() -> None:
    vault = MagicMock()
    vault.list_all_credentials.return_value = []

    assert (
        reveal_external_agent_secret(
            vault,
            CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME,
            workspace_id=str(uuid4()),
        )
        is None
    )
    vault.reveal_secret.assert_not_called()


def test_upsert_external_agent_secret_creates_when_missing() -> None:
    vault = MagicMock()
    vault.list_all_credentials.return_value = []
    workspace_uuid = uuid4()
    workspace_id = str(workspace_uuid)

    upsert_external_agent_secret(
        vault,
        credential_name=GEMINI_STATE_JSON_CREDENTIAL_NAME,
        provider="gemini",
        secret='{"state": {}}',
        workspace_id=workspace_id,
    )

    vault.create_credential.assert_called_once_with(
        name=GEMINI_STATE_JSON_CREDENTIAL_NAME,
        provider="gemini",
        scopes=["worker", "external-agent", "gemini"],
        secret='{"state": {}}',
        actor="external_agent_worker",
        scope=CredentialScope.for_workspaces(workspace_uuid),
        workspace_id=workspace_id,
    )
    vault.update_credential.assert_not_called()


def test_delete_external_agent_secret_removes_all_matches() -> None:
    vault = MagicMock()
    workspace_id = str(uuid4())
    first = SimpleNamespace(
        id=uuid4(),
        name=" gemini_state_json ",
        workspace_id=workspace_id,
    )
    duplicate = SimpleNamespace(
        id=uuid4(),
        name="GEMINI_STATE_JSON",
        workspace_id=workspace_id,
    )
    vault.list_all_credentials.return_value = [first, duplicate]

    deleted = delete_external_agent_secret(
        vault,
        credential_name=GEMINI_STATE_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )

    assert deleted is True
    assert vault.delete_credential.call_args_list == [
        call(first.id),
        call(duplicate.id),
    ]
