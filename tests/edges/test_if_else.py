import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.edges import IfElseEdge
from orcheo.graph.state import State


@pytest.mark.asyncio
async def test_if_else_contains_and_membership_operations() -> None:
    state = State({"node_results": {}})
    contains_node = IfElseEdge(
        name="contains_list",
        conditions=[
            {
                "left": ["alpha", "beta"],
                "operator": "contains",
                "right": "beta",
            }
        ],
    )
    contains_result = await contains_node(state, RunnableConfig())
    assert contains_result == "true"

    not_contains_node = IfElseEdge(
        name="no_match",
        conditions=[
            {
                "left": "Signal",
                "operator": "not_contains",
                "right": "noise",
                "case_sensitive": False,
            }
        ],
    )
    not_contains_result = await not_contains_node(state, RunnableConfig())
    assert not_contains_result == "true"

    in_node = IfElseEdge(
        name="key_lookup",
        conditions=[
            {
                "left": "token",
                "operator": "in",
                "right": {"token": 1},
            }
        ],
    )
    in_result = await in_node(state, RunnableConfig())
    assert in_result == "true"

    not_in_node = IfElseEdge(
        name="missing_key",
        conditions=[
            {
                "left": "gamma",
                "operator": "not_in",
                "right": {"alpha": 1},
            }
        ],
    )
    not_in_result = await not_in_node(state, RunnableConfig())
    assert not_in_result == "true"

    invalid_node = IfElseEdge(
        name="bad_container",
        conditions=[
            {
                "left": object(),
                "operator": "contains",
                "right": "value",
            }
        ],
    )
    with pytest.raises(ValueError):
        await invalid_node(state, RunnableConfig())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("left", "operator", "right", "case_sensitive", "expected"),
    [
        (5, "greater_than", 3, True, True),
        ("Hello", "equals", "hello", True, False),
        ("Hello", "equals", "hello", False, True),
    ],
)
async def test_if_else_node(
    left: object, operator: str, right: object, case_sensitive: bool, expected: bool
) -> None:
    state = State({"node_results": {}})
    node = IfElseEdge(
        name="condition",
        conditions=[
            {
                "left": left,
                "operator": operator,
                "right": right,
                "case_sensitive": case_sensitive,
            }
        ],
    )

    result = await node(state, RunnableConfig())

    assert result == ("true" if expected else "false")


@pytest.mark.asyncio
async def test_if_else_node_combines_multiple_conditions() -> None:
    state = State({"node_results": {}})
    node = IfElseEdge(
        name="multi",
        condition_logic="or",
        conditions=[
            {
                "left": 1,
                "operator": "equals",
                "right": 2,
            },
            {
                "left": 5,
                "operator": "greater_than",
                "right": 4,
            },
        ],
    )

    result = await node(state, RunnableConfig())

    assert result == "true"


@pytest.mark.asyncio
async def test_if_else_node_with_and_logic_all_fail() -> None:
    state = State({"node_results": {}})
    node = IfElseEdge(
        name="multi",
        condition_logic="and",
        conditions=[
            {"left": 1, "operator": "equals", "right": 1},
            {"left": 5, "operator": "equals", "right": 10},
        ],
    )

    result = await node(state, RunnableConfig())

    assert result == "false"


@pytest.mark.asyncio
async def test_if_else_re_resolves_templates_per_invocation() -> None:
    node = IfElseEdge(
        name="condition",
        conditions=[
            {
                "left": "{{loop.done}}",
                "operator": "is_falsy",
            }
        ],
    )

    first = await node(
        State({"node_results": {"loop": {"done": False}}}),
        RunnableConfig(),
    )
    second = await node(
        State({"node_results": {"loop": {"done": True}}}),
        RunnableConfig(),
    )

    assert first == "true"
    assert second == "false"
