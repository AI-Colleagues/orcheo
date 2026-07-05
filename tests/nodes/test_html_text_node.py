"""Tests covering HtmlTextTransformNode behavior."""

from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.data import HtmlTextTransformNode
from orcheo.nodes.data.html_text import _apply_html_text_operations


@pytest.mark.asyncio
async def test_html_text_transform_node_transforms_selected_list_fields() -> None:
    """Selected fields inside list items should be transformed."""
    state = State({"node_results": {}})
    items = [
        {
            "title": "R&amp;D\xa0<launch>",
            "link": "https://example.test/?q=R&D",
        },
        {"title": None, "link": "https://example.test/next"},
    ]
    node = HtmlTextTransformNode(
        name="html_text",
        input_data=items,
        operations=["unescape", "normalize_nbsp", "escape"],
        fields=["title"],
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["html_text"]

    assert payload["result"] == [
        {
            "title": "R&amp;D &lt;launch&gt;",
            "link": "https://example.test/?q=R&D",
        },
        {"title": None, "link": "https://example.test/next"},
    ]


@pytest.mark.asyncio
async def test_html_text_transform_node_transforms_all_strings_by_default() -> None:
    """Omitting fields should transform every string in the payload."""
    state = State({"node_results": {}})
    node = HtmlTextTransformNode(
        name="html_text",
        input_data={"message": "Tom &amp; Jerry", "nested": ["<tag>"]},
        operations=["unescape", "escape"],
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["html_text"]

    assert payload["result"] == {
        "message": "Tom &amp; Jerry",
        "nested": ["&lt;tag&gt;"],
    }


@pytest.mark.asyncio
async def test_html_text_transform_node_leaves_dicts_missing_the_field_untouched() -> (
    None
):
    """Dict items missing the selected field are copied through unchanged."""
    state = State({"node_results": {}})
    items = [{"title": "R&amp;D"}, {"link": "https://example.test"}]
    node = HtmlTextTransformNode(
        name="html_text",
        input_data=items,
        operations=["unescape"],
        fields=["title"],
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["html_text"]

    assert payload["result"] == [
        {"title": "R&D"},
        {"link": "https://example.test"},
    ]


@pytest.mark.asyncio
async def test_html_text_transform_node_leaves_non_container_input_untouched() -> None:
    """A scalar input with a field selector is returned unchanged."""
    state = State({"node_results": {}})
    node = HtmlTextTransformNode(
        name="html_text",
        input_data=42,
        operations=["unescape"],
        fields=["title"],
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["html_text"]

    assert payload["result"] == 42


def test_apply_html_text_operations_ignores_unrecognized_operation() -> None:
    """An operation outside the known set is a no-op, leaving the string as-is."""
    assert (
        _apply_html_text_operations("Tom &amp; Jerry", ["bogus"]) == "Tom &amp; Jerry"
    )
