"""Tests covering MergeNode behavior."""

from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.data import MergeNode


@pytest.mark.asyncio
async def test_merge_node_deep_merges_mappings() -> None:
    """MergeNode should merge dictionaries recursively by default."""

    state = State({"node_results": {}})
    node = MergeNode(
        name="merge",
        items=[
            {"a": 1, "nested": {"x": 1}},
            {"b": 2, "nested": {"y": 2}},
        ],
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["merge"]
    assert payload["result"] == {"a": 1, "b": 2, "nested": {"x": 1, "y": 2}}


@pytest.mark.asyncio
async def test_merge_node_concatenates_lists_with_deduplication() -> None:
    """MergeNode should deduplicate values when requested."""

    state = State({"node_results": {}})
    node = MergeNode(
        name="merge",
        items=[["alpha", "beta"], ["beta", "gamma"]],
        mode="list",
        deduplicate=True,
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["merge"]
    assert payload["result"] == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_merge_node_supports_shallow_updates() -> None:
    """MergeNode should respect the shallow update flag."""

    state = State({"node_results": {}})
    node = MergeNode(
        name="merge",
        items=[{"nested": {"x": 1}}, {"nested": {"y": 2}}],
        deep=False,
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["merge"]
    assert payload["result"] == {"nested": {"y": 2}}


@pytest.mark.asyncio
async def test_merge_node_validates_items_for_lists() -> None:
    """MergeNode should raise when list mode receives invalid items."""

    state = State({"node_results": {}})
    node = MergeNode(name="merge", items=[{"a": 1}], mode="list")

    with pytest.raises(ValueError):
        await node(state, RunnableConfig())


@pytest.mark.asyncio
async def test_merge_node_infers_mode_errors() -> None:
    """MergeNode should error when auto mode cannot infer a strategy."""

    node = MergeNode(name="merge", items=["string"], mode="auto")
    state = State({"node_results": {}})

    with pytest.raises(ValueError):
        await node(state, RunnableConfig())


@pytest.mark.asyncio
async def test_merge_node_auto_detects_list_mode() -> None:
    """MergeNode should infer list mode for sequence inputs."""

    state = State({"node_results": {}})
    node = MergeNode(name="merge", items=[[1, 2], [3]], mode="auto")
    payload = (await node(state, RunnableConfig()))["node_results"]["merge"]
    assert payload["result"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_merge_node_validates_dict_items() -> None:
    """MergeNode should raise when dictionary merge receives invalid items."""

    state = State({"node_results": {}})
    node = MergeNode(name="merge", items=[{"a": 1}, [1, 2]], mode="dict")

    with pytest.raises(ValueError):
        await node(state, RunnableConfig())


@pytest.mark.asyncio
async def test_merge_node_returns_empty_for_no_items() -> None:
    """MergeNode should return an empty structure when nothing is provided."""

    state = State({"node_results": {}})
    node = MergeNode(name="merge", items=[], mode="dict")
    payload = (await node(state, RunnableConfig()))["node_results"]["merge"]
    assert payload["result"] == {}

    list_node = MergeNode(name="merge", items=[], mode="list")
    payload = (await list_node(state, RunnableConfig()))["node_results"]["merge"]
    assert payload["result"] == []
