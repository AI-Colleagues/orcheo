"""Tests for external workflow nodes backed by coding-agent CLIs."""

from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any
import pytest
from orcheo.external_agents.runtime import ExternalAgentRuntimeManager
from orcheo.graph.state import State
from orcheo.nodes.claude_code import ClaudeCodeNode
from orcheo.nodes.codex import CodexNode
from orcheo.nodes.gemini import GeminiNode
from orcheo.nodes.ai.external.claude_code import (
    ClaudeCodeNode as ExternalClaudeCodeNode,
)
from orcheo.nodes.ai.external.codex import CodexNode as ExternalCodexNode
from orcheo.nodes.ai.external.gemini import GeminiNode as ExternalGeminiNode
from orcheo.nodes.registry import registry
from orcheo.runtime.credentials import CredentialReferenceNotFoundError
from orcheo.tracing.model_metadata import TRACE_METADATA_KEY
from tests.external_agents.test_runtime import FakeProvider


class FakeCodexRuntimeManager(ExternalAgentRuntimeManager):
    """Runtime manager configured with the fake provider for node tests."""

    provider = FakeProvider(version="1.0.0", authenticated=True)
    runtime_root: Path

    def __init__(self) -> None:
        """Initialize the fake runtime manager."""
        self.provider.name = "codex"
        super().__init__(
            runtime_root=self.runtime_root,
            providers={"codex": self.provider},
        )


class FakeClaudeRuntimeManager(ExternalAgentRuntimeManager):
    """Runtime manager configured with the fake provider for node tests."""

    provider = FakeProvider(version="1.0.0", authenticated=False)
    runtime_root: Path

    def __init__(self) -> None:
        """Initialize the fake runtime manager."""
        self.provider.name = "claude_code"
        super().__init__(
            runtime_root=self.runtime_root,
            providers={"claude_code": self.provider},
        )


class FakeGeminiRuntimeManager(ExternalAgentRuntimeManager):
    """Runtime manager configured with the fake provider for node tests."""

    provider = FakeProvider(version="1.0.0", authenticated=True)
    runtime_root: Path

    def __init__(self) -> None:
        """Initialize the fake runtime manager."""
        self.provider.name = "gemini"
        super().__init__(
            runtime_root=self.runtime_root,
            providers={"gemini": self.provider},
        )


@pytest.mark.asyncio
async def test_codex_node_successfully_installs_and_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex node installs a missing runtime and returns normalized success data."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
    FakeCodexRuntimeManager.runtime_root = tmp_path / "runtimes"
    monkeypatch.setattr(CodexNode, "runtime_manager_class", FakeCodexRuntimeManager)
    node = CodexNode(
        name="codex_fix",
        prompt="fix tests",
        working_directory=str(repo),
    )

    result = await node(
        State({"inputs": {}, "results": {}, "messages": []}),
        {},
    )

    payload = result["results"]["codex_fix"]
    assert payload["status"] == "succeeded"
    assert payload["provider"] == "codex"
    assert payload["resolved_version"] == "1.0.0"
    assert payload["stdout"] == "fix tests\n"
    assert result[TRACE_METADATA_KEY]["external_agent"]["provider"] == "codex"
    assert result[TRACE_METADATA_KEY]["external_agent"]["command_path"].endswith(
        "fake-agent"
    )


@pytest.mark.asyncio
async def test_claude_node_returns_setup_needed_when_auth_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude node returns structured setup-needed guidance when unauthenticated."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
    FakeClaudeRuntimeManager.runtime_root = tmp_path / "runtimes"
    monkeypatch.setattr(
        ClaudeCodeNode,
        "runtime_manager_class",
        FakeClaudeRuntimeManager,
    )
    node = ClaudeCodeNode(
        name="claude_review",
        prompt="review diff",
        working_directory=str(repo),
    )

    result = await node(
        State({"inputs": {}, "results": {}, "messages": []}),
        {},
    )

    payload = result["results"]["claude_review"]
    assert payload["status"] == "setup_needed"
    assert payload["reason"] == "auth_required"
    assert payload["commands"] == ["fake-agent login"]
    assert payload["resolved_version"] == "1.0.0"


@pytest.mark.asyncio
async def test_gemini_node_successfully_installs_and_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini node installs a missing runtime and returns normalized success data."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
    FakeGeminiRuntimeManager.runtime_root = tmp_path / "runtimes"
    monkeypatch.setattr(GeminiNode, "runtime_manager_class", FakeGeminiRuntimeManager)
    node = GeminiNode(
        name="gemini_review",
        prompt="review diff",
        working_directory=str(repo),
    )

    result = await node(
        State({"inputs": {}, "results": {}, "messages": []}),
        {},
    )

    payload = result["results"]["gemini_review"]
    assert payload["status"] == "succeeded"
    assert payload["provider"] == "gemini"
    assert payload["resolved_version"] == "1.0.0"
    assert payload["stdout"] == "review diff\n"


@pytest.mark.asyncio
async def test_codex_node_normalizes_timeout_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeouts terminate the process tree and retain normalized failure fields."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True)
    FakeCodexRuntimeManager.runtime_root = tmp_path / "runtimes"
    monkeypatch.setattr(CodexNode, "runtime_manager_class", FakeCodexRuntimeManager)
    node = CodexNode(
        name="codex_timeout",
        prompt="timeout please",
        working_directory=str(repo),
        timeout_seconds=1,
    )

    result = await node(
        State({"inputs": {}, "results": {}, "messages": []}),
        {},
    )

    payload = result["results"]["codex_timeout"]
    assert payload["status"] == "failed"
    assert payload["reason"] == "timeout"
    assert payload["message"] == "codex timed out after 1 seconds."


def test_claude_node_auth_environment_overrides_returns_token() -> None:
    node = ClaudeCodeNode(
        name="claude_overrides",
        prompt="cleanup",
        working_directory=".",
        auth_token="sk-ant-FOO",
    )

    assert node.auth_environment_overrides() == {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-FOO"
    }


def test_claude_node_auth_environment_overrides_skips_empty_token() -> None:
    node = ClaudeCodeNode(
        name="claude_overrides",
        prompt="cleanup",
        working_directory=".",
    )
    node.auth_token = None

    assert node.auth_environment_overrides() == {}


def test_codex_node_auth_environment_overrides_returns_json() -> None:
    node = CodexNode(
        name="codex_overrides",
        prompt="cleanup",
        working_directory=".",
        auth_json='{"hello": "world"}',
    )

    assert node.auth_environment_overrides() == {
        "CODEX_AUTH_JSON": '{"hello": "world"}'
    }


def test_codex_node_auth_environment_overrides_skips_empty_json() -> None:
    node = CodexNode(
        name="codex_overrides",
        prompt="cleanup",
        working_directory=".",
    )
    node.auth_json = None

    assert node.auth_environment_overrides() == {}


def test_gemini_node_auth_environment_overrides_returns_files() -> None:
    node = GeminiNode(
        name="gemini_overrides",
        prompt="cleanup",
        working_directory=".",
        auth_json='{"version":1,"files":{"trustedFolders.json":"{\\"trustedFolders\\":[]}"}}',
        google_accounts_json='{"active":{}}',
        state_json='{"tipsShown":{}}',
        oauth_creds_json='{"tokens":{}}',
    )

    assert node.auth_environment_overrides() == {
        "GEMINI_AUTH_JSON": (
            '{"version":1,"files":{"trustedFolders.json":"{\\"trustedFolders\\":[]}"}}'
        ),
        "GEMINI_GOOGLE_ACCOUNTS_JSON": '{"active":{}}',
        "GEMINI_STATE_JSON": '{"tipsShown":{}}',
        "GEMINI_OAUTH_CREDS_JSON": '{"tokens":{}}',
    }


def test_gemini_node_auth_environment_overrides_skips_empty_values() -> None:
    node = GeminiNode(
        name="gemini_overrides",
        prompt="cleanup",
        working_directory=".",
    )
    node.auth_json = None
    node.google_accounts_json = None
    node.state_json = None
    node.oauth_creds_json = None

    assert node.auth_environment_overrides() == {}


def test_external_claude_code_node_auth_environment_overrides_returns_token() -> None:
    node = ExternalClaudeCodeNode(
        name="claude_overrides",
        prompt="cleanup",
        working_directory=".",
        auth_token="sk-ant-EXTERNAL",
    )

    assert node.auth_environment_overrides() == {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-EXTERNAL"
    }


def test_external_claude_code_node_auth_environment_overrides_skips_empty_token() -> (
    None
):
    node = ExternalClaudeCodeNode(
        name="claude_overrides",
        prompt="cleanup",
        working_directory=".",
        auth_token=None,
    )

    assert node.auth_environment_overrides() == {}


def test_external_codex_node_auth_environment_overrides_returns_json() -> None:
    node = ExternalCodexNode(
        name="codex_overrides",
        prompt="cleanup",
        working_directory=".",
        auth_json='{"hello": "external"}',
    )

    assert node.auth_environment_overrides() == {
        "CODEX_AUTH_JSON": '{"hello": "external"}'
    }


def test_external_codex_node_auth_environment_overrides_skips_empty_json() -> None:
    node = ExternalCodexNode(
        name="codex_overrides",
        prompt="cleanup",
        working_directory=".",
        auth_json=None,
    )

    assert node.auth_environment_overrides() == {}


def test_external_gemini_node_auth_environment_overrides_returns_all_values() -> None:
    node = ExternalGeminiNode(
        name="gemini_overrides",
        prompt="cleanup",
        working_directory=".",
        auth_json='{"version":1,"files":{"trustedFolders.json":"{\\"trustedFolders\\":[]}"}}',
        google_accounts_json='{"active":{}}',
        state_json='{"tipsShown":{}}',
        oauth_creds_json='{"tokens":{}}',
    )

    assert node.auth_environment_overrides() == {
        "GEMINI_AUTH_JSON": (
            '{"version":1,"files":{"trustedFolders.json":"{\\"trustedFolders\\":[]}"}}'
        ),
        "GEMINI_GOOGLE_ACCOUNTS_JSON": '{"active":{}}',
        "GEMINI_STATE_JSON": '{"tipsShown":{}}',
        "GEMINI_OAUTH_CREDS_JSON": '{"tokens":{}}',
    }


def test_external_gemini_node_auth_environment_overrides_skips_empty_values() -> None:
    node = ExternalGeminiNode(
        name="gemini_overrides",
        prompt="cleanup",
        working_directory=".",
        auth_json=None,
        google_accounts_json=None,
        state_json=None,
        oauth_creds_json=None,
    )

    assert node.auth_environment_overrides() == {}


def test_compute_run_updates_raises_on_non_optional_credential_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = ClaudeCodeNode(
        name="claude_runs",
        prompt="cleanup",
        working_directory=".",
    )
    state = State({"inputs": {}, "results": {}})
    error = CredentialReferenceNotFoundError("missing")

    def failing_decode(value: Any, _: State) -> Any:
        if value is node.prompt:
            raise error
        return value

    monkeypatch.setattr(node, "_decode_value", failing_decode)

    with pytest.raises(CredentialReferenceNotFoundError):
        node._compute_run_updates(state)


def test_external_agent_nodes_registered() -> None:
    """External agent nodes are registered in the global node registry."""
    claude_metadata = registry.get_metadata("ClaudeCodeNode")
    codex_metadata = registry.get_metadata("CodexNode")
    gemini_metadata = registry.get_metadata("GeminiNode")

    assert claude_metadata is not None
    assert claude_metadata.category == "ai"
    assert codex_metadata is not None
    assert codex_metadata.category == "ai"
    assert gemini_metadata is not None
    assert gemini_metadata.category == "ai"
