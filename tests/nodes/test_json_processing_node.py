"""Tests covering JsonProcessingNode behavior."""

from __future__ import annotations
import json
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.data import JsonProcessingNode


@pytest.mark.asyncio
async def test_json_processing_node_extracts_values() -> None:
    """JsonProcessingNode should extract nested values."""

    state = State({"node_results": {}})
    data = json.dumps({"person": {"name": "Ada", "languages": ["python", "c"]}})
    node = JsonProcessingNode(
        name="json",
        operation="extract",
        input_data=data,
        path="person.languages.0",
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["json"]
    assert payload["result"] == "python"
    assert payload["found"] is True


@pytest.mark.asyncio
async def test_json_processing_node_handles_missing_path() -> None:
    """Missing paths should emit default values."""

    state = State({"node_results": {}})
    node = JsonProcessingNode(
        name="json",
        operation="extract",
        input_data={"alpha": 1},
        path="beta",
        default="fallback",
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["json"]
    assert payload["result"] == "fallback"
    assert payload["found"] is False


@pytest.mark.asyncio
async def test_json_processing_node_stringifies_payloads() -> None:
    """Stringify mode should serialise objects with indentation."""

    state = State({"node_results": {}})
    node = JsonProcessingNode(
        name="json",
        operation="stringify",
        input_data={"alpha": 1},
        indent=0,
        ensure_ascii=True,
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["json"]
    assert json.loads(payload["result"]) == {"alpha": 1}


@pytest.mark.asyncio
async def test_json_processing_node_parses_inputs() -> None:
    """Parse mode should handle string and native inputs."""

    state = State({"node_results": {}})
    node_text = JsonProcessingNode(name="json", operation="parse", input_data="1")
    node_dict = JsonProcessingNode(
        name="json", operation="parse", input_data={"k": "v"}
    )

    text_payload = (await node_text(state, RunnableConfig()))["node_results"]["json"]
    dict_payload = (await node_dict(state, RunnableConfig()))["node_results"]["json"]

    assert text_payload["result"] == 1
    assert dict_payload["result"] == {"k": "v"}


@pytest.mark.asyncio
async def test_json_processing_node_requires_path() -> None:
    """Extract mode without a path should raise an error."""

    state = State({"node_results": {}})
    node = JsonProcessingNode(name="json", operation="extract", input_data={})

    with pytest.raises(ValueError):
        await node(state, RunnableConfig())


@pytest.mark.asyncio
async def test_json_processing_node_rejects_unknown_operation() -> None:
    """An unsupported operation should raise an error."""

    node = JsonProcessingNode(name="json", operation="parse", input_data={})
    node.operation = "invalid"  # type: ignore[assignment]
    state = State({"node_results": {}})

    with pytest.raises(ValueError):
        await node(state, RunnableConfig())
