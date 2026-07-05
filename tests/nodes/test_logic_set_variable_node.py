from collections import OrderedDict
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.logic import DelayNode, ForLoopNode, SetVariableNode, _build_nested


@pytest.mark.asyncio
async def test_set_variable_node_stores_multiple_variables() -> None:
    state = State({"node_results": {}})
    node = SetVariableNode(
        name="assign",
        variables={
            "user_name": "Ada",
            "user_age": 30,
            "user_active": True,
            "user_tags": ["admin", "developer"],
        },
    )

    result = await node(state, RunnableConfig())
    payload = result["node_results"]["assign"]

    assert payload == {
        "user_name": "Ada",
        "user_age": 30,
        "user_active": True,
        "user_tags": ["admin", "developer"],
    }


@pytest.mark.asyncio
async def test_set_variable_node_handles_nested_dicts() -> None:
    state = State({"node_results": {}})
    node = SetVariableNode(
        name="assign",
        variables={
            "user": {"name": "Ada", "role": "admin"},
            "settings": {"theme": "dark", "notifications": True},
        },
    )

    result = await node(state, RunnableConfig())
    payload = result["node_results"]["assign"]

    assert payload["user"]["name"] == "Ada"
    assert payload["settings"]["theme"] == "dark"


@pytest.mark.asyncio
async def test_set_variable_node_supports_dotted_paths() -> None:
    state = State({"node_results": {}})
    node = SetVariableNode(
        name="assign",
        variables={
            "profile": {"role": "builder"},
            "profile.name": "Ada",
            "profile.stats.score": 42,
            "flags.is_active": True,
        },
    )

    result = await node(state, RunnableConfig())
    payload = result["node_results"]["assign"]

    assert payload["profile"]["role"] == "builder"
    assert payload["profile"]["name"] == "Ada"
    assert payload["profile"]["stats"]["score"] == 42
    assert payload["flags"]["is_active"] is True


@pytest.mark.asyncio
async def test_set_variable_node_merges_existing_dicts() -> None:
    state = State({"node_results": {}})
    node = SetVariableNode(
        name="assign",
        variables=OrderedDict(
            [
                ("profile.name", "Ada"),
                ("profile", {"role": "builder"}),
            ]
        ),
    )

    result = await node(state, RunnableConfig())
    payload = result["node_results"]["assign"]

    assert payload["profile"]["name"] == "Ada"
    assert payload["profile"]["role"] == "builder"


@pytest.mark.asyncio
async def test_set_variable_node_empty_variables() -> None:
    state = State({"node_results": {}})
    node = SetVariableNode(name="assign", variables={})

    result = await node(state, RunnableConfig())
    payload = result["node_results"]["assign"]

    assert payload == {}


@pytest.mark.asyncio
async def test_delay_node_runs() -> None:
    state = State({"node_results": {}})
    node = DelayNode(name="wait", duration_seconds=0.0)

    result = await node(state, RunnableConfig())

    assert result["node_results"]["wait"]["duration_seconds"] == 0.0


@pytest.mark.asyncio
async def test_for_loop_node_non_list_items() -> None:
    """Non-list items returns done immediately without iterating."""
    node = ForLoopNode(name="loop", items="not_a_list")

    result = await node.run({"node_results": {}}, RunnableConfig())

    assert result == {"done": True, "index": 0, "total": 0, "current_item": None}


@pytest.mark.asyncio
async def test_for_loop_node_first_iteration() -> None:
    """First invocation yields the item at index 0 and advances index to 1."""
    state = State({"node_results": {}})
    node = ForLoopNode(name="loop", items=["a", "b", "c"])

    result = await node(state, RunnableConfig())
    payload = result["node_results"]["loop"]

    assert payload["done"] is False
    assert payload["current_item"] == "a"
    assert payload["index"] == 1
    assert payload["total"] == 3


@pytest.mark.asyncio
async def test_for_loop_node_subsequent_iteration() -> None:
    """Reads the previous index from state and advances through the list."""
    state = State({"node_results": {"loop": {"index": 1}}})
    node = ForLoopNode(name="loop", items=["a", "b", "c"])

    result = await node(state, RunnableConfig())
    payload = result["node_results"]["loop"]

    assert payload["done"] is False
    assert payload["current_item"] == "b"
    assert payload["index"] == 2


@pytest.mark.asyncio
async def test_for_loop_node_exhausted() -> None:
    """Returns done=True when the stored index has reached or passed the end."""
    state = State({"node_results": {"loop": {"index": 3}}})
    node = ForLoopNode(name="loop", items=["a", "b", "c"])

    result = await node(state, RunnableConfig())
    payload = result["node_results"]["loop"]

    assert payload["done"] is True
    assert payload["current_item"] is None
    assert payload["index"] == 3
    assert payload["total"] == 3


@pytest.mark.asyncio
async def test_for_loop_node_prev_results_not_mapping() -> None:
    """When results is not a Mapping the index defaults to 0."""
    node = ForLoopNode(name="loop", items=["x", "y"])

    result = await node.run({"results": "not_a_dict"}, RunnableConfig())

    assert result["current_item"] == "x"
    assert result["index"] == 1


@pytest.mark.asyncio
async def test_for_loop_node_loop_state_not_mapping() -> None:
    """When the node's own previous state is not a Mapping the index defaults to 0."""
    node = ForLoopNode(name="loop", items=["x", "y"])

    result = await node.run({"node_results": {"loop": "not_a_dict"}}, RunnableConfig())

    assert result["current_item"] == "x"
    assert result["index"] == 1


@pytest.mark.asyncio
async def test_for_loop_node_raw_index_none() -> None:
    """When the stored index value is None it defaults to 0."""
    node = ForLoopNode(name="loop", items=["x", "y"])

    result = await node.run(
        {"node_results": {"loop": {"index": None}}}, RunnableConfig()
    )

    assert result["current_item"] == "x"
    assert result["index"] == 1


@pytest.mark.asyncio
async def test_for_loop_node_re_resolves_templated_items_per_invocation() -> None:
    """Templated item sources should be resolved fresh for each invocation."""
    node = ForLoopNode(name="loop", items="{{source.items}}")

    first = await node(
        State({"node_results": {"source": {"items": ["a"]}}}),
        RunnableConfig(),
    )
    second = await node(
        State({"node_results": {"source": {"items": ["b"]}}}),
        RunnableConfig(),
    )

    assert first["node_results"]["loop"]["current_item"] == "a"
    assert second["node_results"]["loop"]["current_item"] == "b"


def test_build_nested_empty_path_raises() -> None:
    """An empty path string raises ValueError."""
    with pytest.raises(ValueError, match="non-empty string"):
        _build_nested("", 42)


def test_build_nested_whitespace_only_path_raises() -> None:
    """A path containing only dots/whitespace (no real segments) raises ValueError."""
    with pytest.raises(ValueError, match="at least one segment"):
        _build_nested("...", 42)
