"""Tests covering DataTransformNode behavior."""

from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.data import DataTransformNode, FieldTransform


@pytest.mark.asyncio
async def test_data_transform_node_applies_mappings() -> None:
    """DataTransformNode should remap fields and apply transforms."""

    state = State({"node_results": {}})
    node = DataTransformNode(
        name="transform",
        input_data={"user": {"name": "Ada", "age": "37"}},
        transforms=[
            FieldTransform(
                source="user.name",
                target="profile.full_name",
                transform="upper",
            ),
            FieldTransform(
                source="user.age",
                target="profile.age",
                transform="int",
            ),
        ],
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["transform"]
    assert payload["result"] == {"profile": {"full_name": "ADA", "age": 37}}


@pytest.mark.asyncio
async def test_data_transform_node_supports_conversions() -> None:
    """DataTransformNode should handle conversion transforms."""

    state = State({"node_results": {}})
    node = DataTransformNode(
        name="convert",
        input_data={"value": "5", "text": "Ada", "items": [1, 2]},
        transforms=[
            FieldTransform(source="value", target="numeric.int", transform="int"),
            FieldTransform(source="value", target="numeric.float", transform="float"),
            FieldTransform(source="value", target="numeric.bool", transform="bool"),
            FieldTransform(source="text", target="text.lower", transform="lower"),
            FieldTransform(source="text", target="text.upper", transform="upper"),
            FieldTransform(source="text", target="text.title", transform="title"),
            FieldTransform(source="items", target="counts.length", transform="length"),
            FieldTransform(source=None, target="defaults.message", default="hi"),
        ],
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["convert"]
    assert payload["result"] == {
        "numeric": {"int": 5, "float": 5.0, "bool": True},
        "text": {"lower": "ada", "upper": "ADA", "title": "Ada"},
        "counts": {"length": 2},
        "defaults": {"message": "hi"},
    }


@pytest.mark.asyncio
async def test_data_transform_node_skips_missing_values() -> None:
    """Missing fields should be skipped when configured."""

    state = State({"node_results": {}})
    node = DataTransformNode(
        name="skip",
        input_data={},
        transforms=[
            FieldTransform(
                source="missing",
                target="result.value",
                when_missing="skip",
            )
        ],
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["skip"]
    assert payload["result"] == {}


@pytest.mark.asyncio
async def test_data_transform_node_uses_default_for_missing_values() -> None:
    """Missing fields should use default when when_missing is 'default'."""

    state = State({"node_results": {}})
    node = DataTransformNode(
        name="defaults",
        input_data={"existing": "value"},
        transforms=[
            FieldTransform(
                source="missing_field",
                target="result.value",
                when_missing="default",
                default="default_value",
            )
        ],
    )

    payload = (await node(state, RunnableConfig()))["node_results"]["defaults"]
    assert payload["result"] == {"result": {"value": "default_value"}}
