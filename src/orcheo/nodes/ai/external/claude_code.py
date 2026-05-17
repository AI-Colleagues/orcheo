"""Claude Code workflow node."""

from __future__ import annotations
from pydantic import Field
from orcheo.nodes.ai.external.base import ExternalAgentNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="ClaudeCodeNode",
        description="Execute Claude Code as a non-interactive coding-agent step.",
        category="ai",
    )
)
class ClaudeCodeNode(ExternalAgentNode):
    """Workflow node for the Claude Code CLI."""

    provider_name = "claude_code"
    optional_auth_fields = frozenset({"auth_token"})

    auth_token: str | None = Field(
        default="[[CLAUDE_CODE_OAUTH_TOKEN]]",
        description=(
            "Optional Claude Code OAuth token placeholder resolved from the vault."
        ),
    )

    def auth_environment_overrides(self) -> dict[str, str]:
        """Materialize Claude auth from the configured credential template."""
        if not self.auth_token:
            return {}
        return {"CLAUDE_CODE_OAUTH_TOKEN": self.auth_token}
