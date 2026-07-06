"""Tests for MongoDBInsertManyNode."""

from __future__ import annotations
from typing import Any
from unittest.mock import Mock
import pytest
from langchain_core.runnables import RunnableConfig
from pymongo.results import InsertManyResult
from orcheo.graph.state import State
from orcheo.nodes.storage.mongodb import MongoDBInsertManyNode
from tests.nodes.conftest import MongoTestContext


_MOCK_IDS = ["id0", "id1"]

_RECORDS = [
    {
        "text": "Hello world",
        "values": [0.1, 0.2, 0.3],
        "metadata": {
            "source": "https://example.com",
            "document_id": "doc-0",
            "chunk_index": 0,
        },
    },
    {
        "text": "Another chunk",
        "values": [0.4, 0.5, 0.6],
        "metadata": {
            "source": "https://example.com",
            "document_id": "doc-0",
            "chunk_index": 1,
        },
    },
]


def _base_state() -> State:
    return State(messages=[], inputs={}, node_results={})


def _state_with_records(
    *,
    source_key: str = "chunk_embedding",
    embeddings_field: str = "chunk_embeddings",
    embedding_name: str | None = "dense",
    records: list[dict[str, Any]] | None = None,
) -> State:
    recs = records if records is not None else list(_RECORDS)
    if embedding_name is not None:
        payload: Any = {embeddings_field: {embedding_name: recs}}
    else:
        payload = {embeddings_field: recs}
    return State(messages=[], inputs={}, node_results={source_key: payload})


def _mock_insert_result() -> Mock:
    result = Mock(spec=InsertManyResult)
    result.inserted_ids = list(_MOCK_IDS)
    return result


def _build_node(**overrides: Any) -> MongoDBInsertManyNode:
    defaults: dict[str, Any] = {
        "name": "insert_node",
        "database": "test_db",
        "collection": "test_coll",
        "source_result_key": "chunk_embedding",
    }
    defaults.update(overrides)
    return MongoDBInsertManyNode(**defaults)


@pytest.mark.asyncio
async def test_insert_many_with_vectors(
    mongo_context: MongoTestContext,
) -> None:
    mongo_context.collection.insert_many.return_value = _mock_insert_result()

    node = _build_node(
        embedding_name="dense",
        vector_field="embedding",
        text_field="body",
    )

    result = await node.run(_state_with_records(), RunnableConfig())

    assert result["inserted_count"] == 2
    assert result["inserted_ids"] == _MOCK_IDS

    docs = mongo_context.collection.insert_many.call_args[0][0]
    assert len(docs) == 2
    assert docs[0]["body"] == "Hello world"
    assert docs[0]["embedding"] == [0.1, 0.2, 0.3]
    assert docs[0]["source"] == "https://example.com"
    assert docs[0]["document_id"] == "doc-0"
    assert docs[0]["chunk_index"] == 0


@pytest.mark.asyncio
async def test_insert_many_without_vectors(
    mongo_context: MongoTestContext,
) -> None:
    mongo_context.collection.insert_many.return_value = _mock_insert_result()

    node = _build_node(embedding_name="dense")

    result = await node.run(_state_with_records(), RunnableConfig())

    assert result["inserted_count"] == 2
    docs = mongo_context.collection.insert_many.call_args[0][0]
    assert "embedding" not in docs[0]
    assert docs[0]["text"] == "Hello world"
    assert docs[0]["source"] == "https://example.com"


@pytest.mark.asyncio
async def test_insert_many_nested_metadata(
    mongo_context: MongoTestContext,
) -> None:
    mongo_context.collection.insert_many.return_value = _mock_insert_result()

    node = _build_node(embedding_name="dense", metadata_field="meta")

    await node.run(_state_with_records(), RunnableConfig())

    docs = mongo_context.collection.insert_many.call_args[0][0]
    assert "source" not in docs[0]
    assert docs[0]["meta"] == {
        "source": "https://example.com",
        "document_id": "doc-0",
        "chunk_index": 0,
    }


@pytest.mark.asyncio
async def test_insert_many_no_metadata(
    mongo_context: MongoTestContext,
) -> None:
    mongo_context.collection.insert_many.return_value = _mock_insert_result()

    node = _build_node(embedding_name="dense", include_metadata=False)

    await node.run(_state_with_records(), RunnableConfig())

    docs = mongo_context.collection.insert_many.call_args[0][0]
    assert docs[0] == {"text": "Hello world"}
    assert "source" not in docs[0]
    assert "metadata" not in docs[0]


@pytest.mark.asyncio
async def test_insert_many_no_embedding_name(
    mongo_context: MongoTestContext,
) -> None:
    """When embedding_name is None, records are read directly as a list."""
    mongo_context.collection.insert_many.return_value = _mock_insert_result()

    node = _build_node(embedding_name=None)
    state = _state_with_records(embedding_name=None)

    await node.run(state, RunnableConfig())

    docs = mongo_context.collection.insert_many.call_args[0][0]
    assert len(docs) == 2
    assert docs[0]["text"] == "Hello world"


@pytest.mark.asyncio
async def test_insert_many_empty_records_raises(
    mongo_context: MongoTestContext,
) -> None:
    node = _build_node(source_result_key="nonexistent")

    with pytest.raises(ValueError, match="No records available to insert"):
        await node.run(_base_state(), RunnableConfig())


@pytest.mark.asyncio
async def test_insert_many_missing_embeddings_field_raises(
    mongo_context: MongoTestContext,
) -> None:
    state = State(
        messages=[],
        inputs={},
        node_results={"chunk_embedding": {"wrong_field": []}},
    )
    node = _build_node(embedding_name="dense")

    with pytest.raises(ValueError, match="No records available to insert"):
        await node.run(state, RunnableConfig())


@pytest.mark.asyncio
async def test_insert_many_custom_text_field(
    mongo_context: MongoTestContext,
) -> None:
    mongo_context.collection.insert_many.return_value = _mock_insert_result()

    node = _build_node(
        embedding_name="dense",
        text_field="content",
        include_metadata=False,
    )

    await node.run(_state_with_records(), RunnableConfig())

    docs = mongo_context.collection.insert_many.call_args[0][0]
    assert "content" in docs[0]
    assert "text" not in docs[0]
    assert docs[0]["content"] == "Hello world"


@pytest.mark.asyncio
async def test_insert_many_non_mapping_results_raises(
    mongo_context: MongoTestContext,
) -> None:
    """Line 625: results is not a Mapping → returns [] → raises."""
    state = State(messages=[], inputs={}, node_results="not-a-mapping")  # type: ignore[arg-type]
    node = _build_node(embedding_name="dense")

    with pytest.raises(ValueError, match="No records available to insert"):
        await node.run(state, RunnableConfig())


@pytest.mark.asyncio
async def test_insert_many_records_not_list_raises(
    mongo_context: MongoTestContext,
) -> None:
    """Line 637: records is not a list → returns [] → raises."""
    state = State(
        messages=[],
        inputs={},
        node_results={
            "chunk_embedding": {
                "chunk_embeddings": {"dense": "not-a-list"},
            },
        },
    )
    node = _build_node(embedding_name="dense")

    with pytest.raises(ValueError, match="No records available to insert"):
        await node.run(state, RunnableConfig())
