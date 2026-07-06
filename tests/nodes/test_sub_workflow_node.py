import copy
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.sub_workflow import SubWorkflowNode


@pytest.mark.asyncio
async def test_sub_workflow_node_runs_steps_and_propagates() -> None:
    """SubWorkflowNode should execute configured steps sequentially."""

    node = SubWorkflowNode(
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
async def test_sub_workflow_node_validates_step_configuration() -> None:
    """SubWorkflowNode should validate the supplied steps."""

    node = SubWorkflowNode(
        name="sub",
        steps=[{"name": "invalid"}],
    )

    state = State({"node_results": {}})
    with pytest.raises(ValueError):
        await node(state, RunnableConfig())


@pytest.mark.asyncio
async def test_sub_workflow_node_empty_steps() -> None:
    """SubWorkflowNode should handle empty steps list."""

    node = SubWorkflowNode(
        name="sub",
        steps=[],
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["sub"]

    assert payload == {"steps": [], "result": None}


@pytest.mark.asyncio
async def test_sub_workflow_node_unknown_node_type() -> None:
    """SubWorkflowNode should raise ValueError for unknown node type."""

    node = SubWorkflowNode(
        name="sub",
        steps=[{"type": "NonExistentNode", "name": "test"}],
    )

    state = State({"node_results": {}})
    with pytest.raises(ValueError, match="Unknown node type"):
        await node(state, RunnableConfig())


@pytest.mark.asyncio
async def test_sub_workflow_node_propagate_updates_parent() -> None:
    """SubWorkflowNode should propagate results to parent state when enabled."""

    node = SubWorkflowNode(
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

    state = State({"node_results": {"existing": "data"}})
    await node(state, RunnableConfig())

    assert state["node_results"]["step1"] == {"value": 42}
    assert state["node_results"]["existing"] == "data"


@pytest.mark.asyncio
async def test_sub_workflow_node_custom_result_step() -> None:
    """SubWorkflowNode should return result from specified step."""

    node = SubWorkflowNode(
        name="sub",
        steps=[
            {
                "type": "SetVariableNode",
                "name": "first",
                "variables": {"value": 1},
            },
            {
                "type": "SetVariableNode",
                "name": "second",
                "variables": {"value": 2},
            },
        ],
        result_step="first",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["sub"]

    assert payload["result"] == {"value": 1}


@pytest.mark.asyncio
async def test_sub_workflow_node_include_state_disabled() -> None:
    """SubWorkflowNode should not include state when disabled."""

    node = SubWorkflowNode(
        name="sub",
        steps=[
            {
                "type": "SetVariableNode",
                "name": "step1",
                "variables": {"value": 42},
            },
        ],
        include_state=False,
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["sub"]

    assert "state" not in payload


@pytest.mark.asyncio
async def test_sub_workflow_node_propagate_replaces_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SubWorkflowNode should replace parent results when not a Mapping."""

    original_deepcopy = copy.deepcopy

    def mock_deepcopy(obj):
        if obj == "not_a_dict":
            return {}
        return original_deepcopy(obj)

    monkeypatch.setattr("copy.deepcopy", mock_deepcopy)

    node = SubWorkflowNode(
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
