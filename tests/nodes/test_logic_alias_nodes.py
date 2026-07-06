"""Coverage tests for legacy logic module aliases."""

from __future__ import annotations

import copy
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.logic.debug import DebugNode as LogicDebugNode
from orcheo.nodes.logic.sub_workflow import SubWorkflowNode as LogicSubWorkflowNode


@pytest.mark.asyncio
async def test_logic_debug_node_covers_message_and_state_branches() -> None:
    node = LogicDebugNode(
        name="debug",
        message="Inspect value",
        tap_path="items.1.value",
        include_state=True,
    )

    state = State({"node_results": {"items": [{"value": 2}, {"value": 5}]}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["found"] is True
    assert payload["value"] == 5
    assert payload["state"]["node_results"]["items"][1]["value"] == 5


@pytest.mark.asyncio
async def test_logic_debug_node_covers_empty_message_path() -> None:
    node = LogicDebugNode(name="debug")

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["message"] is None
    assert payload["tap_path"] is None
    assert payload["found"] is False


@pytest.mark.asyncio
async def test_logic_sub_workflow_node_covers_full_execution() -> None:
    node = LogicSubWorkflowNode(
        name="sub",
        steps=[
            {
                "type": "SetVariableNode",
                "name": "initial",
                "variables": {"value": 3},
            },
            {
                "type": "SetVariableNode",
                "name": "derived",
                "variables": {
                    "value": "{{node_results.initial.value }}",
                    "extra": 9,
                },
            },
        ],
        include_state=True,
        propagate_to_parent=True,
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["sub"]

    assert payload["result"] == {"value": 3, "extra": 9}
    assert [step["name"] for step in payload["steps"]] == ["initial", "derived"]
    assert state["node_results"]["derived"] == {"value": 3, "extra": 9}
    assert payload["state"]["node_results"]["derived"]["extra"] == 9


@pytest.mark.asyncio
async def test_logic_sub_workflow_node_covers_validation_and_empty_paths() -> None:
    empty = LogicSubWorkflowNode(name="sub", steps=[])
    state = State({"node_results": {}})
    assert (await empty(state, RunnableConfig()))["node_results"]["sub"] == {
        "steps": [],
        "result": None,
    }

    invalid = LogicSubWorkflowNode(name="sub", steps=[{"name": "invalid"}])
    with pytest.raises(ValueError):
        await invalid(state, RunnableConfig())

    unknown = LogicSubWorkflowNode(
        name="sub",
        steps=[{"type": "NonExistentNode", "name": "test"}],
    )
    with pytest.raises(ValueError, match="Unknown node type"):
        await unknown(state, RunnableConfig())


@pytest.mark.asyncio
async def test_logic_sub_workflow_node_covers_no_propagation_and_no_state() -> None:
    node = LogicSubWorkflowNode(
        name="sub",
        steps=[
            {
                "type": "SetVariableNode",
                "name": "step1",
                "variables": {"value": 42},
            },
        ],
        propagate_to_parent=False,
        include_state=False,
    )

    state = State({"node_results": {"existing": "data"}})
    payload = (await node(state, RunnableConfig()))["node_results"]["sub"]

    assert payload["result"] == {"value": 42}
    assert "state" not in payload


@pytest.mark.asyncio
async def test_logic_sub_workflow_node_replaces_non_mapping_parent_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_deepcopy = copy.deepcopy

    def mock_deepcopy(obj):
        if obj == "not_a_dict":
            return {}
        return original_deepcopy(obj)

    monkeypatch.setattr("copy.deepcopy", mock_deepcopy)

    node = LogicSubWorkflowNode(
        name="sub",
        steps=[
            {
                "type": "SetVariableNode",
                "name": "step1",
                "variables": {"value": 42},
            },
        ],
        propagate_to_parent=True,
    )

    state = State({"node_results": "not_a_dict"})

    await node(state, RunnableConfig())

    assert isinstance(state["node_results"], dict)
    assert state["node_results"]["step1"] == {"value": 42}
