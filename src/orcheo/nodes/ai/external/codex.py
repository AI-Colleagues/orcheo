"""Codex workflow node."""

from __future__ import annotations
from pydantic import Field
from orcheo.nodes.ai.external.base import ExternalAgentNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="CodexNode",
        description="Execute Codex as a non-interactive coding-agent step.",
        category="ai",
    )
)
class CodexNode(ExternalAgentNode):
    """Workflow node for the Codex CLI."""

    provider_name = "codex"
    optional_auth_fields = frozenset({"auth_json"})

    auth_json: str | None = Field(
        default="[[CODEX_AUTH_JSON]]",
        description=("Optional Codex auth.json placeholder resolved from the vault."),
    )

    def auth_environment_overrides(self) -> dict[str, str]:
        """Materialize Codex auth.json content from the configured template."""
        if not self.auth_json:
            return {}
        return {"CODEX_AUTH_JSON": self.auth_json}
