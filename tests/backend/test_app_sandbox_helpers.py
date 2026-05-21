"""Tests for the backend-shared sandbox bootstrap helpers."""

from __future__ import annotations
from types import SimpleNamespace
from typing import Any
import pytest
from orcheo_backend.app import sandbox as sandbox_module
from orcheo_backend.app.sandbox import (
    build_credential_broker,
    build_workflow_run_spec,
    collect_node_types,
    ensure_sandbox_configured,
    run_uses_trusted_nodes_only,
)


def test_run_uses_trusted_nodes_only_returns_true_for_trusted_only() -> None:
    assert run_uses_trusted_nodes_only(("AINode", "ChatModelNode"))


def test_run_uses_trusted_nodes_only_returns_false_for_unknown_type() -> None:
    assert not run_uses_trusted_nodes_only(("AINode", "TenantPythonNode"))


def test_run_uses_trusted_nodes_only_fails_closed_on_empty() -> None:
    """A graph with no parseable node types must not take the in-worker fast path."""
    assert not run_uses_trusted_nodes_only(())


def test_collect_node_types_returns_empty_for_non_dict() -> None:
    assert collect_node_types("not a dict") == ()


def test_collect_node_types_extracts_unique_types() -> None:
    config = {
        "nodes": [
            {"type": "AINode"},
            {"kind": "RSSNode"},
            {"type": "AINode"},  # duplicate filtered
            "not-a-dict",
            {"name": "no-type-here"},
        ]
    }
    assert collect_node_types(config) == ("AINode", "RSSNode")


def test_build_workflow_run_spec_carries_node_types() -> None:
    spec = build_workflow_run_spec(
        execution_id="exec-1",
        workspace_id="ws-1",
        graph_config={"nodes": [{"type": "TenantPythonNode"}]},
        inputs={"x": 1},
        runnable_config={"configurable": {"thread_id": "exec-1"}},
        state_config={"configurable": {"ai_model": "openai:test"}},
    )
    assert spec.run_id == "exec-1"
    assert spec.workspace_id == "ws-1"
    assert spec.node_types == ("TenantPythonNode",)
    assert spec.runnable_config == {"configurable": {"thread_id": "exec-1"}}
    assert spec.state_config == {"configurable": {"ai_model": "openai:test"}}


def test_ensure_sandbox_configured_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated calls must not raise and must not rebind an existing broker."""
    calls: list[Any] = []

    class _StubBootstrap:
        def __init__(self) -> None:
            self._broker: Any = None

        def configure(self, broker: Any) -> None:
            calls.append(broker)
            self._broker = broker

    stub = _StubBootstrap()
    monkeypatch.setattr(sandbox_module, "_bootstrap", stub)

    # Provide a fake vault so build_credential_broker doesn't blow up.
    monkeypatch.setattr(sandbox_module, "get_vault", lambda: SimpleNamespace())
    monkeypatch.setenv("ORCHEO_CREDENTIAL_BROKER_SECRET", "abc")

    ensure_sandbox_configured()
    ensure_sandbox_configured()  # idempotent — must not rebind

    assert len(calls) == 1


def test_build_credential_broker_requires_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The broker refuses to build without ORCHEO_CREDENTIAL_BROKER_SECRET."""
    monkeypatch.delenv("ORCHEO_CREDENTIAL_BROKER_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="ORCHEO_CREDENTIAL_BROKER_SECRET"):
        build_credential_broker()
