"""Vault-backed auth helpers for worker-scoped external agent providers."""

from __future__ import annotations
from typing import Final
from uuid import UUID
from orcheo.external_agents.providers.gemini import (
    GEMINI_AUTH_JSON_ENV_VAR,
    GEMINI_GOOGLE_ACCOUNTS_JSON_ENV_VAR,
    GEMINI_OAUTH_CREDS_JSON_ENV_VAR,
    GEMINI_STATE_JSON_ENV_VAR,
)
from orcheo.models import CredentialMetadata, CredentialScope
from orcheo.vault import BaseCredentialVault


CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME: Final[str] = "CLAUDE_CODE_OAUTH_TOKEN"
CODEX_AUTH_JSON_CREDENTIAL_NAME: Final[str] = "CODEX_AUTH_JSON"
CODEX_AUTH_JSON_ENV_VAR: Final[str] = "CODEX_AUTH_JSON"
GEMINI_AUTH_JSON_CREDENTIAL_NAME: Final[str] = "GEMINI_AUTH_JSON"
GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME: Final[str] = "GEMINI_GOOGLE_ACCOUNTS_JSON"
GEMINI_STATE_JSON_CREDENTIAL_NAME: Final[str] = "GEMINI_STATE_JSON"
GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME: Final[str] = "GEMINI_OAUTH_CREDS_JSON"
EXTERNAL_AGENT_VAULT_ACTOR: Final[str] = "external_agent_worker"


def load_external_agent_vault_environment(
    vault: BaseCredentialVault,
    *,
    workspace_id: str | None = None,
) -> dict[str, str]:
    """Return environment overrides materialized from the configured vault."""
    environ: dict[str, str] = {}
    claude_token = reveal_external_agent_secret(
        vault,
        CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    if claude_token:
        environ["CLAUDE_CODE_OAUTH_TOKEN"] = claude_token

    codex_auth_json = reveal_external_agent_secret(
        vault,
        CODEX_AUTH_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    if codex_auth_json:
        environ[CODEX_AUTH_JSON_ENV_VAR] = codex_auth_json

    gemini_auth_json = reveal_external_agent_secret(
        vault,
        GEMINI_AUTH_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    if gemini_auth_json:
        environ[GEMINI_AUTH_JSON_ENV_VAR] = gemini_auth_json

    gemini_google_accounts_json = reveal_external_agent_secret(
        vault,
        GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    if gemini_google_accounts_json:
        environ[GEMINI_GOOGLE_ACCOUNTS_JSON_ENV_VAR] = gemini_google_accounts_json

    gemini_state_json = reveal_external_agent_secret(
        vault,
        GEMINI_STATE_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    if gemini_state_json:
        environ[GEMINI_STATE_JSON_ENV_VAR] = gemini_state_json

    gemini_oauth_creds_json = reveal_external_agent_secret(
        vault,
        GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME,
        workspace_id=workspace_id,
    )
    if gemini_oauth_creds_json:
        environ[GEMINI_OAUTH_CREDS_JSON_ENV_VAR] = gemini_oauth_creds_json
    return environ


def reveal_external_agent_secret(
    vault: BaseCredentialVault,
    credential_name: str,
    *,
    workspace_id: str | None = None,
) -> str | None:
    """Return the secret for ``credential_name`` when it exists."""
    metadata = _find_named_credential(
        vault,
        credential_name,
        workspace_id=workspace_id,
    )
    if metadata is None:
        return None
    return vault.reveal_secret(credential_id=metadata.id)


def upsert_external_agent_secret(
    vault: BaseCredentialVault,
    *,
    credential_name: str,
    provider: str,
    secret: str,
    actor: str = EXTERNAL_AGENT_VAULT_ACTOR,
    workspace_id: str | None = None,
) -> None:
    """Create or update one workspace-scoped external-agent secret."""
    matches = _find_named_credentials(
        vault,
        credential_name,
        workspace_id=workspace_id,
    )
    existing = matches[0] if matches else None
    if existing is None:
        credential_scope = (
            CredentialScope.for_workspaces(UUID(workspace_id))
            if workspace_id is not None
            else CredentialScope.unrestricted()
        )
        vault.create_credential(
            name=credential_name,
            provider=provider,
            scopes=["worker", "external-agent", provider],
            secret=secret,
            actor=actor,
            scope=credential_scope,
            workspace_id=workspace_id,
        )
        return

    credential_scope = (
        CredentialScope.for_workspaces(UUID(workspace_id))
        if workspace_id is not None
        else CredentialScope.unrestricted()
    )
    vault.update_credential(
        credential_id=existing.id,
        actor=actor,
        provider=provider,
        secret=secret,
        scope=credential_scope,
    )
    for duplicate in matches[1:]:
        vault.delete_credential(duplicate.id)


def delete_external_agent_secret(
    vault: BaseCredentialVault,
    *,
    credential_name: str,
    workspace_id: str | None = None,
) -> bool:
    """Delete all stored secrets for ``credential_name`` if they exist."""
    matches = _find_named_credentials(
        vault,
        credential_name,
        workspace_id=workspace_id,
    )
    deleted = False
    for metadata in matches:
        vault.delete_credential(metadata.id)
        deleted = True
    return deleted


def _find_named_credential(
    vault: BaseCredentialVault,
    credential_name: str,
    *,
    workspace_id: str | None = None,
) -> CredentialMetadata | None:
    matches = _find_named_credentials(
        vault,
        credential_name,
        workspace_id=workspace_id,
    )
    return matches[0] if matches else None


def _find_named_credentials(
    vault: BaseCredentialVault,
    credential_name: str,
    *,
    workspace_id: str | None = None,
) -> list[CredentialMetadata]:
    normalized_name = credential_name.strip().lower()
    return [
        metadata
        for metadata in vault.list_all_credentials(workspace_id=workspace_id)
        if metadata.name.strip().lower() == normalized_name
        and (
            (workspace_id is None and metadata.workspace_id is None)
            or (workspace_id is not None and metadata.workspace_id == workspace_id)
        )
    ]


__all__ = [
    "CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME",
    "CODEX_AUTH_JSON_CREDENTIAL_NAME",
    "CODEX_AUTH_JSON_ENV_VAR",
    "GEMINI_AUTH_JSON_CREDENTIAL_NAME",
    "GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME",
    "GEMINI_STATE_JSON_CREDENTIAL_NAME",
    "GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME",
    "EXTERNAL_AGENT_VAULT_ACTOR",
    "delete_external_agent_secret",
    "load_external_agent_vault_environment",
    "reveal_external_agent_secret",
    "upsert_external_agent_secret",
]
