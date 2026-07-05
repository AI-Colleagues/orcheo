import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.edges import SwitchEdge
from orcheo.graph.state import State


@pytest.mark.asyncio
async def test_switch_node_casefolds_strings() -> None:
    state = State({"node_results": {}})
    node = SwitchEdge(
        name="router",
        value="Completed",
        case_sensitive=False,
        cases=[{"match": "completed", "branch_key": "completed"}],
    )

    result = await node(state, RunnableConfig())

    assert result == "completed"


@pytest.mark.asyncio
async def test_switch_node_formats_special_values() -> None:
    state = State({"node_results": {}})
    node = SwitchEdge(
        name="router",
        value=None,
        cases=[{"match": True, "branch_key": "truthy"}],
        default_branch_key="fallback",
    )

    result = await node(state, RunnableConfig())
    assert result == "fallback"


@pytest.mark.asyncio
async def test_switch_node_matches_first_successful_case() -> None:
    state = State({"node_results": {}})
    node = SwitchEdge(
        name="router",
        value="beta",
        cases=[
            {"match": "alpha", "branch_key": "alpha"},
            {"match": "beta", "branch_key": "beta", "label": "Second"},
        ],
    )

    result = await node(state, RunnableConfig())
    assert result == "beta"


@pytest.mark.asyncio
async def test_switch_node_case_sensitive_override() -> None:
    state = State({"node_results": {}})
    node = SwitchEdge(
        name="router",
        value="TEST",
        case_sensitive=False,
        cases=[
            {"match": "wrong", "branch_key": "first"},
            {"match": "test", "branch_key": "second"},
        ],
    )

    result = await node(state, RunnableConfig())

    assert result == "second"
