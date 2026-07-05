import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.edges import WhileEdge
from orcheo.graph.state import State


@pytest.mark.asyncio
async def test_while_node_iterations_and_limit() -> None:
    node = WhileEdge(
        name="loop",
        conditions=[{"operator": "less_than", "right": 2}],
        max_iterations=2,
    )

    # Iteration 0: 0 < 2 is True, 0 < max_iterations=2, continue
    state = State({"node_results": {"loop": {"iteration": 0}}})
    first = await node(state, RunnableConfig())
    assert first == "continue"

    # Iteration 1: 1 < 2 is True, 1 < max_iterations=2, continue
    state = State({"node_results": {"loop": {"iteration": 1}}})
    second = await node(state, RunnableConfig())
    assert second == "continue"

    # Iteration 2: at max_iterations=2, exit
    state = State({"node_results": {"loop": {"iteration": 2}}})
    third = await node(state, RunnableConfig())
    assert third == "exit"


def test_while_node_current_iteration_reads_state() -> None:
    node = WhileEdge(name="loop")
    state = {"node_results": {"loop": {"iteration": 5}}}
    assert node._current_iteration(state) == 5

    empty_state = {"node_results": {"loop": {"iteration": "x"}}}
    assert node._current_iteration(empty_state) == 0

    missing_node_results_state: dict = {}
    assert node._current_iteration(missing_node_results_state) == 0


@pytest.mark.asyncio
async def test_while_node_with_or_logic() -> None:
    state = State({"node_results": {}})
    node = WhileEdge(
        name="loop",
        conditions=[
            {"operator": "equals", "right": 5},
            {"operator": "less_than", "right": 3},
        ],
        condition_logic="or",
    )

    first = await node(state, RunnableConfig())
    assert first == "continue"


@pytest.mark.asyncio
async def test_while_node_without_max_iterations() -> None:
    state = State({"node_results": {}})
    node = WhileEdge(
        name="loop",
        conditions=[{"operator": "less_than", "right": 5}],
    )

    first = await node(state, RunnableConfig())
    assert first == "continue"


@pytest.mark.asyncio
async def test_while_node_missing_node_results_handled_gracefully() -> None:
    """Test that While edge handles missing node_results dict gracefully."""
    state = State({"inputs": {}})  # No node_results dict
    node = WhileEdge(
        name="loop",
        conditions=[{"operator": "less_than", "right": 5}],
    )

    first = await node(state, RunnableConfig())
    # iteration defaults to 0, 0 < 5 → continue
    assert first == "continue"
