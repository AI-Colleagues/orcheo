"""Tests covering HtmlTextTransformNode behavior."""

from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.data import HtmlTextTransformNode


@pytest.mark.asyncio
async def test_html_text_transform_node_transforms_selected_list_fields() -> None:
    """Selected fields inside list items should be transformed."""
    state = State({"results": {}})
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

    payload = (await node(state, RunnableConfig()))["results"]["html_text"]

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
    state = State({"results": {}})
    node = HtmlTextTransformNode(
        name="html_text",
        input_data={"message": "Tom &amp; Jerry", "nested": ["<tag>"]},
        operations=["unescape", "escape"],
    )

    payload = (await node(state, RunnableConfig()))["results"]["html_text"]

    assert payload["result"] == {
        "message": "Tom &amp; Jerry",
        "nested": ["&lt;tag&gt;"],
    }
