"""Miscellaneous MongoDBNode conversion and lifecycle tests."""

from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING, cast
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State


if TYPE_CHECKING:
    from tests.nodes.conftest import MongoTestContext


def test_convert_primitive_to_dict(mongo_context: MongoTestContext) -> None:
    """Primitive values should be wrapped in a result dict."""

    node = mongo_context.build_node(operation="count_documents")
    assert node._convert_result_to_dict(42) == {"result": 42}
    assert node._convert_result_to_dict("test_string") == {"result": "test_string"}
    assert node._convert_result_to_dict(True) == {"result": True}
    assert node._convert_result_to_dict(3.14) == {"result": 3.14}


def test_convert_none_to_dict(mongo_context: MongoTestContext) -> None:
    """None results should be wrapped for consistency."""

    node = mongo_context.build_node(operation="find_one")
    assert node._convert_result_to_dict(None) == {"result": None}


def test_run_method(mongo_context: MongoTestContext) -> None:
    """The run method should execute operations and format responses."""

    mongo_context.collection.find.return_value = [{"_id": "1", "name": "doc1"}]
    node = mongo_context.build_node(operation="find", filter={"status": "active"})

    state = State(messages=[], inputs={}, node_results={})
    config = cast(RunnableConfig, {})

    result = asyncio.run(node.run(state, config))
    assert isinstance(result, dict)
    assert "data" in result
    assert result["data"] == [{"_id": "1", "name": "doc1"}]
    mongo_context.collection.find.assert_called_once_with({"status": "active"})


def test_del_method(mongo_context: MongoTestContext) -> None:
    """__del__ should close the Mongo client when present."""

    node = mongo_context.build_node(operation="find")
    node._ensure_collection()
    node.__del__()
    mongo_context.client.close.assert_called_once()


def test_ensure_collection_client_already_exists(
    mongo_context: MongoTestContext,
) -> None:
    """_ensure_collection should reuse existing clients and collections."""

    node = mongo_context.build_node(operation="find")
    node._ensure_collection()
    existing_client = node._client
    existing_collection = node._collection

    node._ensure_collection()
    assert node._client is existing_client
    assert node._collection is existing_collection
    assert mongo_context.mongo_client.call_count == 1


def test_ensure_collection_collection_already_exists(
    mongo_context: MongoTestContext,
) -> None:
    """_ensure_collection should not recreate clients when already set."""

    node = mongo_context.build_node(operation="find")
    node._client = mongo_context.client
    assert node._collection is None

    node._ensure_collection()
    assert node._client is mongo_context.client
    assert node._collection is not None
    mongo_context.mongo_client.assert_not_called()
