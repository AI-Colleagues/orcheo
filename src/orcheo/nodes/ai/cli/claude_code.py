"""Claude Code CLI workflow node."""

from __future__ import annotations
from orcheo.nodes.ai.cli.base import CLIAgentNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="ClaudeCodeNode",
        description=(
            "Execute Claude Code as a non-interactive coding-agent step. Runs "
            "unsandboxed with the worker's privileges; trusted workflows only."
        ),
        category="ai",
        restricted=True,
    )
)
class ClaudeCodeNode(CLIAgentNode):
    """Workflow node for a pre-authenticated, host-installed Claude Code CLI.

    Invoked with ``--dangerously-skip-permissions``: the agent runs with no
    permission prompts and the worker's host privileges. See
    :class:`CLIAgentNode` for the runtime-input warning — never feed unsanitized
    external data into ``prompt``/``system_prompt``/``working_directory``.
    """

    executable_name = "claude"

    def build_command(self, executable: str) -> list[str]:
        """Build a non-interactive Claude Code invocation."""
        command = [
            executable,
            "--dangerously-skip-permissions",
            "--print",
            self.prompt,
            "--output-format",
            "text",
        ]
        if self.system_prompt:
            command.extend(["--append-system-prompt", self.system_prompt])
        return command
