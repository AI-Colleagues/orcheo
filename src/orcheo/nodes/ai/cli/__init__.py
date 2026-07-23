"""Host-installed CLI coding-agent nodes."""

from __future__ import annotations
from orcheo.nodes.ai.cli.antigravity import AntigravityNode
from orcheo.nodes.ai.cli.base import CLIAgentNode
from orcheo.nodes.ai.cli.claude_code import ClaudeCodeNode
from orcheo.nodes.ai.cli.codex import CodexNode


__all__ = [
    "AntigravityNode",
    "CLIAgentNode",
    "ClaudeCodeNode",
    "CodexNode",
]
