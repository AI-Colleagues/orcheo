"""Gemini CLI workflow node."""

from __future__ import annotations
from pydantic import Field
from orcheo.external_agents.providers.gemini import (
    GEMINI_AUTH_JSON_ENV_VAR,
    GEMINI_GOOGLE_ACCOUNTS_JSON_ENV_VAR,
    GEMINI_OAUTH_CREDS_JSON_ENV_VAR,
    GEMINI_STATE_JSON_ENV_VAR,
)
from orcheo.nodes.ai.external.base import ExternalAgentNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="GeminiNode",
        description="Execute Gemini CLI as a non-interactive coding-agent step.",
        category="ai",
    )
)
class GeminiNode(ExternalAgentNode):
    """Workflow node for the Gemini CLI."""

    provider_name = "gemini"
    optional_auth_fields = frozenset(
        {"auth_json", "google_accounts_json", "state_json", "oauth_creds_json"}
    )

    auth_json: str | None = Field(
        default="[[GEMINI_AUTH_JSON]]",
        description="Optional bundled Gemini auth payload resolved from the vault.",
    )

    google_accounts_json: str | None = Field(
        default="[[GEMINI_GOOGLE_ACCOUNTS_JSON]]",
        description=(
            "Optional Gemini google_accounts.json placeholder resolved from the vault."
        ),
    )
    state_json: str | None = Field(
        default="[[GEMINI_STATE_JSON]]",
        description="Optional Gemini state.json placeholder resolved from the vault.",
    )
    oauth_creds_json: str | None = Field(
        default="[[GEMINI_OAUTH_CREDS_JSON]]",
        description=(
            "Optional Gemini oauth_creds.json placeholder resolved from the vault."
        ),
    )

    def auth_environment_overrides(self) -> dict[str, str]:
        """Materialize Gemini auth files from the configured templates."""
        overrides: dict[str, str] = {}
        if self.auth_json:
            overrides[GEMINI_AUTH_JSON_ENV_VAR] = self.auth_json
        if self.google_accounts_json:
            overrides[GEMINI_GOOGLE_ACCOUNTS_JSON_ENV_VAR] = self.google_accounts_json
        if self.state_json:
            overrides[GEMINI_STATE_JSON_ENV_VAR] = self.state_json
        if self.oauth_creds_json:
            overrides[GEMINI_OAUTH_CREDS_JSON_ENV_VAR] = self.oauth_creds_json
        return overrides
