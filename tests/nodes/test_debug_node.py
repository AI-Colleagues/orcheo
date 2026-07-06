import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.debug import DebugNode


@pytest.mark.asyncio
async def test_debug_node_taps_state_path() -> None:
    """DebugNode should tap into nested state values and include snapshots."""

    node = DebugNode(
        name="debug",
        message="Inspect value",
        tap_path="items.1.value",
        include_state=True,
    )

    state = State({"node_results": {"items": [{"value": 2}, {"value": 5}]}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["message"] == "Inspect value"
    assert payload["found"] is True and payload["value"] == 5
    assert payload["state"]["node_results"]["items"][1]["value"] == 5


@pytest.mark.asyncio
async def test_debug_node_empty_path_error() -> None:
    """DebugNode should raise ValueError for empty tap_path."""

    node = DebugNode(
        name="debug",
        tap_path="",
    )

    state = State({"node_results": {}})
    with pytest.raises(ValueError, match="tap_path must be a non-empty string"):
        await node(state, RunnableConfig())


@pytest.mark.asyncio
async def test_debug_node_whitespace_only_path_error() -> None:
    """DebugNode should raise ValueError for whitespace-only tap_path."""

    node = DebugNode(
        name="debug",
        tap_path="   ",
    )

    state = State({"node_results": {}})
    with pytest.raises(ValueError, match="tap_path must contain at least one segment"):
        await node(state, RunnableConfig())


@pytest.mark.asyncio
async def test_debug_node_invalid_sequence_index() -> None:
    """DebugNode should handle invalid sequence index gracefully."""

    node = DebugNode(
        name="debug",
        tap_path="items.invalid_index",
    )

    state = State({"node_results": {"items": [1, 2, 3]}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["found"] is False
    assert payload["value"] is None


@pytest.mark.asyncio
async def test_debug_node_out_of_bounds_index() -> None:
    """DebugNode should handle out-of-bounds sequence index."""

    node = DebugNode(
        name="debug",
        tap_path="items.10",
    )

    state = State({"node_results": {"items": [1, 2, 3]}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["found"] is False
    assert payload["value"] is None


@pytest.mark.asyncio
async def test_debug_node_negative_index() -> None:
    """DebugNode should handle negative sequence index."""

    node = DebugNode(
        name="debug",
        tap_path="items.-1",
    )

    state = State({"node_results": {"items": [1, 2, 3]}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["found"] is False
    assert payload["value"] is None


@pytest.mark.asyncio
async def test_debug_node_path_not_found() -> None:
    """DebugNode should handle non-existent path."""

    node = DebugNode(
        name="debug",
        tap_path="nonexistent.path",
    )

    state = State({"node_results": {"items": [1, 2, 3]}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["found"] is False
    assert payload["value"] is None


@pytest.mark.asyncio
async def test_debug_node_no_tap_path() -> None:
    """DebugNode should work without tap_path."""

    node = DebugNode(
        name="debug",
        message="Just a message",
    )

    state = State({"node_results": {}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["message"] == "Just a message"
    assert payload["tap_path"] is None
    assert payload["found"] is False


@pytest.mark.asyncio
async def test_debug_node_include_state_disabled() -> None:
    """DebugNode should not include state when disabled."""

    node = DebugNode(
        name="debug",
        message="Test",
        include_state=False,
    )

    state = State({"node_results": {"data": "value"}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert "state" not in payload


@pytest.mark.asyncio
async def test_debug_node_with_message_only() -> None:
    """DebugNode should log message when message is set but tap_path is not."""

    node = DebugNode(
        name="debug",
        message="Debug message only",
        tap_path=None,
    )

    state = State({"node_results": {"data": "value"}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["message"] == "Debug message only"
    assert payload["tap_path"] is None


@pytest.mark.asyncio
async def test_debug_node_logs_with_message_and_tap_path() -> None:
    """DebugNode should log when both message and tap_path are provided."""

    node = DebugNode(
        name="debug",
        message="Checking value",
        tap_path="data.value",
    )

    state = State({"node_results": {"data": {"value": 123}}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["message"] == "Checking value"
    assert payload["tap_path"] == "data.value"
    assert payload["found"] is True
    assert payload["value"] == 123


@pytest.mark.asyncio
async def test_debug_node_normalise_state_with_mapping_inputs() -> None:
    """DebugNode should normalise state with Mapping inputs/results."""

    node = DebugNode(
        name="debug",
        message="State snapshot test",
        include_state=True,
    )

    state = State(
        {
            "inputs": {"param1": "value1", "param2": "value2"},
            "node_results": {"data": {"nested": "value"}},
        }
    )
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert "state" in payload
    assert payload["state"]["inputs"] == {"param1": "value1", "param2": "value2"}
    assert payload["state"]["node_results"]["data"]["nested"] == "value"


@pytest.mark.asyncio
async def test_debug_node_normalise_state_with_non_mapping_results() -> None:
    """DebugNode should handle state with non-Mapping results."""

    node = DebugNode(
        name="debug",
        message="State snapshot with non-dict results",
        include_state=True,
    )

    state = State(
        {
            "inputs": {"param1": "value1"},
            "results": ["item1", "item2"],
        }
    )
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert "state" in payload
    assert payload["state"]["inputs"] == {"param1": "value1"}
    assert payload["state"]["node_results"] == {}


@pytest.mark.asyncio
async def test_debug_node_no_message_no_tap_path() -> None:
    """DebugNode should work without message or tap_path."""

    node = DebugNode(
        name="debug",
        message=None,
        tap_path=None,
    )

    state = State({"node_results": {"data": "value"}})
    payload = (await node(state, RunnableConfig()))["node_results"]["debug"]

    assert payload["message"] is None
    assert payload["tap_path"] is None
    assert payload["found"] is False
