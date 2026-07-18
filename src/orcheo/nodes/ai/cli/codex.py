"""Codex CLI workflow node."""

from __future__ import annotations
from orcheo.nodes.ai.cli.base import CLIAgentNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="CodexNode",
        description="Execute the Codex CLI as a non-interactive coding-agent step.",
        category="ai",
        restricted=True,
    )
)
class CodexNode(CLIAgentNode):
    """Workflow node for a pre-authenticated, host-installed Codex CLI."""

    executable_name = "codex"

    def build_command(self, executable: str) -> list[str]:
        """Build a non-interactive Codex CLI invocation."""
        combined_prompt = self.prompt
        if self.system_prompt:
            combined_prompt = (
                f"System instructions:\n{self.system_prompt.strip()}\n\n"
                f"Task:\n{self.prompt}"
            )
        return [
            executable,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            combined_prompt,
        ]
