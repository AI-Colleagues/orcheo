"""Antigravity CLI workflow node."""

from __future__ import annotations
from orcheo.nodes.ai.cli.base import CLIAgentNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="AntigravityNode",
        description=(
            "Execute the Antigravity CLI as a non-interactive coding-agent step. "
            "Runs unsandboxed with the worker's privileges; trusted workflows only."
        ),
        category="ai",
        restricted=True,
    )
)
class AntigravityNode(CLIAgentNode):
    """Workflow node for a pre-authenticated, host-installed Antigravity CLI.

    Invoked with ``--dangerously-skip-permissions``: the agent runs with no
    permission prompts and the worker's host privileges. See
    :class:`CLIAgentNode` for the runtime-input warning — never feed unsanitized
    external data into ``prompt``/``system_prompt``/``working_directory``.
    """

    executable_name = "agy"

    def build_command(self, executable: str) -> list[str]:
        """Build a non-interactive Antigravity CLI invocation."""
        return [
            executable,
            "--dangerously-skip-permissions",
            "--print-timeout",
            f"{self.timeout_seconds}s",
            "--print",
            self._combined_prompt(),
        ]
