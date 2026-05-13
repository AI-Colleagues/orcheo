"""Tests for shared runtime initial-state construction."""

from __future__ import annotations
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT
from orcheo.runtime.state_builder import build_initial_state


def test_build_initial_state_langgraph_script_mapping_defaults() -> None:
    inputs = {"message": "hello"}

    state = build_initial_state({"format": LANGGRAPH_SCRIPT_FORMAT}, inputs, None)

    assert state["message"] == "hello"
    assert state["inputs"] == inputs
    assert state["results"] == {}
    assert state["messages"] == []
    assert state["config"] == {}


def test_build_initial_state_langgraph_script_mapping_uses_runtime_config() -> None:
    inputs = {"message": "hello"}
    runtime_config = {"configurable": {"thread_id": "exec-1"}}

    state = build_initial_state(
        {"format": LANGGRAPH_SCRIPT_FORMAT},
        inputs,
        runtime_config,
    )

    assert state["config"] == runtime_config


def test_build_initial_state_langgraph_script_overwrites_workspace_id() -> None:
    inputs = {"message": "hello", "workspace_id": "spoofed"}

    state = build_initial_state(
        {"format": LANGGRAPH_SCRIPT_FORMAT},
        inputs,
        None,
        workspace_id="trusted-workspace",
    )

    assert state["workspace_id"] == "trusted-workspace"


def test_build_initial_state_langgraph_script_non_mapping_passthrough() -> None:
    payload = ["message"]

    state = build_initial_state({"format": LANGGRAPH_SCRIPT_FORMAT}, payload, None)

    assert state is payload


def test_build_initial_state_default_shape() -> None:
    inputs = {"message": "hello"}
    runtime_config = {"run_name": "test"}

    state = build_initial_state({"format": "graph"}, inputs, runtime_config)

    assert state["inputs"] == inputs
    assert state["results"] == {}
    assert state["messages"] == []
    assert state["config"] == runtime_config
