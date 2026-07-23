"""Codex CLI workflow node."""

from __future__ import annotations
from orcheo.nodes.ai.cli.base import CLIAgentNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="CodexNode",
        description=(
            "Execute the Codex CLI as a non-interactive coding-agent step. Runs "
            "unsandboxed with the worker's privileges; trusted workflows only."
        ),
        category="ai",
        restricted=True,
    )
)
class CodexNode(CLIAgentNode):
    """Workflow node for a pre-authenticated, host-installed Codex CLI.

    Invoked with ``--dangerously-bypass-approvals-and-sandbox``: the agent runs
    with no sandbox and the worker's host privileges. See :class:`CLIAgentNode`
    for the runtime-input warning — never feed unsanitized external data into
    ``prompt``/``system_prompt``/``working_directory``.
    """

    executable_name = "codex"

    def build_command(self, executable: str) -> list[str]:
        """Build a non-interactive Codex CLI invocation."""
        return [
            executable,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            self._combined_prompt(),
        ]
