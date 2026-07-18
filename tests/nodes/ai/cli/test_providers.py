"""Tests covering CLI invocation building for CLI agent provider nodes."""

from __future__ import annotations
from orcheo.nodes.ai.cli.antigravity import AntigravityNode
from orcheo.nodes.ai.cli.claude_code import ClaudeCodeNode
from orcheo.nodes.ai.cli.codex import CodexNode
from orcheo.nodes.registry import registry


def test_codex_node_build_command_without_system_prompt() -> None:
    node = CodexNode(name="codex", prompt="fix the bug")
    command = node.build_command("/usr/bin/codex")
    assert command == [
        "/usr/bin/codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "fix the bug",
    ]


def test_codex_node_build_command_with_system_prompt() -> None:
    node = CodexNode(name="codex", prompt="fix the bug", system_prompt="Be terse.")
    command = node.build_command("/usr/bin/codex")
    assert command[-1] == "System instructions:\nBe terse.\n\nTask:\nfix the bug"


def test_claude_code_node_build_command_without_system_prompt() -> None:
    node = ClaudeCodeNode(name="claude", prompt="fix the bug")
    command = node.build_command("/usr/bin/claude")
    assert command == [
        "/usr/bin/claude",
        "--dangerously-skip-permissions",
        "--print",
        "fix the bug",
        "--output-format",
        "text",
    ]


def test_claude_code_node_build_command_with_system_prompt() -> None:
    node = ClaudeCodeNode(
        name="claude", prompt="fix the bug", system_prompt="Be terse."
    )
    command = node.build_command("/usr/bin/claude")
    assert command[-2:] == ["--append-system-prompt", "Be terse."]


def test_antigravity_node_build_command_without_system_prompt() -> None:
    node = AntigravityNode(
        name="antigravity", prompt="fix the bug", timeout_seconds=900
    )
    command = node.build_command("/usr/bin/agy")
    assert command == [
        "/usr/bin/agy",
        "--dangerously-skip-permissions",
        "--print-timeout",
        "900s",
        "--print",
        "fix the bug",
    ]


def test_antigravity_node_build_command_with_system_prompt() -> None:
    node = AntigravityNode(
        name="antigravity", prompt="fix the bug", system_prompt="Be terse."
    )
    command = node.build_command("/usr/bin/agy")
    assert command[-1] == "System instructions:\nBe terse.\n\nTask:\nfix the bug"


def test_provider_nodes_are_registered_restricted_ai_nodes() -> None:
    for node_type in ("CodexNode", "ClaudeCodeNode", "AntigravityNode"):
        metadata = registry.get_metadata(node_type)
        assert metadata is not None
        assert metadata.category == "ai"
        assert metadata.restricted is True
