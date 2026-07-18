"""Antigravity CLI workflow node."""

from __future__ import annotations
from orcheo.nodes.ai.cli.base import CLIAgentNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="AntigravityNode",
        description=(
            "Execute the Antigravity CLI as a non-interactive coding-agent step."
        ),
        category="ai",
        restricted=True,
    )
)
class AntigravityNode(CLIAgentNode):
    """Workflow node for a pre-authenticated, host-installed Antigravity CLI."""

    executable_name = "agy"

    def build_command(self, executable: str) -> list[str]:
        """Build a non-interactive Antigravity CLI invocation."""
        combined_prompt = self.prompt
        if self.system_prompt:
            combined_prompt = (
                f"System instructions:\n{self.system_prompt.strip()}\n\n"
                f"Task:\n{self.prompt}"
            )
        return [
            executable,
            "--dangerously-skip-permissions",
            "--print-timeout",
            f"{self.timeout_seconds}s",
            "--print",
            combined_prompt,
        ]
