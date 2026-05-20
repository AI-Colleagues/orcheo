"""Unit tests for the top-level external agent node implementation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from orcheo.external_agents.models import (
    ProcessExecutionResult,
    ResolvedRuntime,
    RuntimeInstallError,
    RuntimeManifest,
    RuntimeResolution,
    RuntimeVerificationError,
)
from orcheo.graph.state import State
from orcheo.nodes.external_agent import ExternalAgentNode
from orcheo.runtime.credentials import CredentialReferenceNotFoundError

from tests.nodes.test_external_agent_node import DummyProvider, FakeRuntimeManager


class LegacyDummyExternalAgentNode(ExternalAgentNode):
    provider_name = DummyProvider.name


def _make_runtime_resolution(tmp_path: Path) -> RuntimeResolution:
    runtime_dir = tmp_path / "legacy-runtime"
    return RuntimeResolution(
        runtime=ResolvedRuntime(
            provider=DummyProvider.name,
            version="0.0.1",
            install_dir=runtime_dir,
            executable_path=runtime_dir / "bin" / DummyProvider.executable_name,
            package_name="@tests/dummy",
        ),
        manifest=RuntimeManifest(
            provider=DummyProvider.name,
            provider_root=tmp_path / "provider",
            current_version="0.0.1",
            current_runtime_path=runtime_dir,
        ),
        maintenance_due=False,
    )


def _make_state(inputs: dict[str, Any] | None = None) -> State:
    return State(inputs=inputs or {}, results={}, structured_response=None, config={})


def _make_node(manager: FakeRuntimeManager) -> LegacyDummyExternalAgentNode:
    class NodeWithManager(LegacyDummyExternalAgentNode):
        runtime_manager_class = staticmethod(lambda: manager)

    node = NodeWithManager(name="legacy-node", prompt="run")
    node.working_directory = "workspace"
    return node


def test_resolve_prompt_and_working_directory_inputs() -> None:
    node = LegacyDummyExternalAgentNode(name="test", prompt="  hello  ")
    assert node._resolve_prompt(_make_state()) == "hello"

    node = LegacyDummyExternalAgentNode(name="test")
    assert node._resolve_prompt(_make_state({"query": "  world  "})) == "world"

    node = LegacyDummyExternalAgentNode(name="test", working_directory="  /tmp  ")
    assert node._resolve_working_directory_input(_make_state()) == "/tmp"

    node = LegacyDummyExternalAgentNode(name="test")
    assert (
        node._resolve_working_directory_input(_make_state({"repo_path": " /repo "}))
        == "/repo"
    )


def test_resolve_prompt_and_working_directory_require_values() -> None:
    node = LegacyDummyExternalAgentNode(name="test")
    with pytest.raises(ValueError):
        node._resolve_prompt(_make_state())
    with pytest.raises(ValueError):
        node._resolve_working_directory_input(_make_state())

    with pytest.raises(ValueError):
        node._resolve_prompt({"inputs": ["not", "a", "mapping"]})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        node._resolve_working_directory_input(  # type: ignore[arg-type]
            {"inputs": ["not", "a", "mapping"]}
        )


def test_compute_run_updates_handles_optional_and_required_placeholders() -> None:
    class OptionalAuthNode(LegacyDummyExternalAgentNode):
        optional_auth_fields = frozenset({"system_prompt"})

    node = OptionalAuthNode(name="test")
    node.system_prompt = "missing-secret"

    def optional_decode(value: object, state: State) -> object:
        del state
        if value == "missing-secret":
            raise CredentialReferenceNotFoundError("missing")
        return value

    node._decode_value = optional_decode  # type: ignore[method-assign]
    updates = node._compute_run_updates(_make_state())
    assert updates["system_prompt"] is None

    node = LegacyDummyExternalAgentNode(name="test")
    node.system_prompt = "missing-secret"
    node._decode_value = optional_decode  # type: ignore[method-assign]
    with pytest.raises(CredentialReferenceNotFoundError):
        node._compute_run_updates(_make_state())


@pytest.mark.asyncio
async def test_run_reports_invalid_configuration(tmp_path: Path) -> None:
    resolution = _make_runtime_resolution(tmp_path)
    manager = FakeRuntimeManager(
        provider=DummyProvider(),
        resolution=resolution,
        raise_validate_error=True,
    )
    node = _make_node(manager)

    result = await node.run(_make_state({"prompt": "run"}), RunnableConfig())

    assert result["reason"] == "invalid_configuration"
    assert "invalid workspace" in result["message"]


@pytest.mark.asyncio
async def test_run_reports_install_and_verification_failures(tmp_path: Path) -> None:
    install_manager = FakeRuntimeManager(
        provider=DummyProvider(),
        resolve_error=RuntimeInstallError(
            "dummy_agent",
            "install failed",
            command=["install"],
            stdout="out",
            stderr="err",
        ),
    )
    node = _make_node(install_manager)
    result = await node.run(_make_state({"prompt": "run"}), RunnableConfig())
    assert result["reason"] == "install_failed"
    assert result["command"] == ["install"]

    verification_manager = FakeRuntimeManager(
        provider=DummyProvider(),
        resolve_error=RuntimeVerificationError("failed"),
    )
    node = _make_node(verification_manager)
    result = await node.run(_make_state({"prompt": "run"}), RunnableConfig())
    assert result["reason"] == "runtime_verification_failed"


@pytest.mark.asyncio
async def test_run_requires_auth_before_execution(tmp_path: Path) -> None:
    resolution = _make_runtime_resolution(tmp_path)
    manager = FakeRuntimeManager(
        provider=DummyProvider(authenticated=False),
        resolution=resolution,
    )
    node = _make_node(manager)

    result = await node.run(_make_state({"prompt": "run"}), RunnableConfig())

    assert result["status"] == "setup_needed"
    assert result["commands"] == ["dummy login"]


@pytest.mark.asyncio
async def test_run_reports_timeout_non_zero_exit_and_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resolution = _make_runtime_resolution(tmp_path)
    manager = FakeRuntimeManager(provider=DummyProvider(), resolution=resolution)
    node = _make_node(manager)

    async def fake_timeout(*args: object, **kwargs: object) -> ProcessExecutionResult:
        del args, kwargs
        return ProcessExecutionResult(
            command=["dummy"],
            stdout="",
            stderr="",
            exit_code=None,
            timed_out=True,
            duration_seconds=0,
        )

    monkeypatch.setattr("orcheo.nodes.external_agent.execute_process", fake_timeout)
    result = await node.run(_make_state({"prompt": "run"}), RunnableConfig())
    assert result["reason"] == "timeout"
    assert result["status"] == "failed"

    async def fake_exit(*args: object, **kwargs: object) -> ProcessExecutionResult:
        del args, kwargs
        return ProcessExecutionResult(
            command=["dummy"],
            stdout="",
            stderr="",
            exit_code=5,
            timed_out=False,
            duration_seconds=0,
        )

    monkeypatch.setattr("orcheo.nodes.external_agent.execute_process", fake_exit)
    result = await node.run(_make_state({"prompt": "run"}), RunnableConfig())
    assert result["reason"] == "non_zero_exit"
    assert "exited with code 5" in result["message"]

    provider = DummyProvider()
    provider.audit_metadata = {
        "event": "external_agent_bypass_flags_used",
        "provider": "dummy_agent",
        "bypass_flags": ["--dangerous"],
        "security_boundary": "container_isolation",
    }
    manager = FakeRuntimeManager(provider=provider, resolution=resolution)
    node = _make_node(manager)

    async def fake_success(*args: object, **kwargs: object) -> ProcessExecutionResult:
        del args, kwargs
        return ProcessExecutionResult(
            command=["dummy"],
            stdout="ok",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0,
        )

    monkeypatch.setattr("orcheo.nodes.external_agent.execute_process", fake_success)
    state = _make_state({"prompt": "run"})
    state["workspace_id"] = "workspace-1"
    result = await node.run(state, RunnableConfig())
    assert result["status"] == "succeeded"
    assert result["stdout"] == "ok"
    assert manager.workspace_id == "workspace-1"


@pytest.mark.asyncio
async def test_run_logs_bypass_flag_audit_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolution = _make_runtime_resolution(tmp_path)
    provider = DummyProvider()
    provider.audit_metadata = {
        "event": "external_agent_bypass_flags_used",
        "provider": "dummy_agent",
        "bypass_flags": ["--dangerous"],
        "security_boundary": "container_isolation",
    }
    manager = FakeRuntimeManager(provider=provider, resolution=resolution)
    node = _make_node(manager)

    async def fake_execute(*args: object, **kwargs: object) -> ProcessExecutionResult:
        del args, kwargs
        return ProcessExecutionResult(
            command=["dummy"],
            stdout="ok",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0,
        )

    monkeypatch.setattr("orcheo.nodes.external_agent.execute_process", fake_execute)

    with caplog.at_level(logging.INFO, logger="orcheo.nodes.external_agent"):
        result = await node.run(_make_state({"prompt": "run"}), RunnableConfig())

    assert result["status"] == "succeeded"
    record = next(
        r
        for r in caplog.records
        if r.message == "External agent execution requested provider bypass flags."
    )
    assert record.event == "external_agent_bypass_flags_used"
    assert record.provider == "dummy_agent"
    assert record.bypass_flags == ["--dangerous"]
    assert record.security_boundary == "container_isolation"
    assert record.node_name == "legacy-node"


@pytest.mark.asyncio
async def test_run_uses_launcher_when_active_launcher_is_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When an active sandbox launcher is set, run() delegates execution to it."""
    resolution = _make_runtime_resolution(tmp_path)
    manager = FakeRuntimeManager(provider=DummyProvider(), resolution=resolution)
    node = _make_node(manager)
    state = _make_state({"prompt": "run"})
    state["workspace_id"] = "ws-sandbox"  # top-level key, not inside inputs

    fake_result = ProcessExecutionResult(
        command=["dummy"],
        stdout="launched-via-sandbox",
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0,
    )

    class _FakeLauncher:
        async def run(self, **kwargs: object) -> ProcessExecutionResult:
            return fake_result

    monkeypatch.setattr(
        "orcheo.nodes.external_agent.get_active_launcher",
        lambda: _FakeLauncher(),
    )

    result = await node.run(state, RunnableConfig())

    assert result["status"] == "succeeded"
    assert result["stdout"] == "launched-via-sandbox"
