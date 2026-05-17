"""Tests for MongoDB Atlas Search nodes."""

from __future__ import annotations
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest
from bson import ObjectId
from langchain_core.runnables import RunnableConfig
from pymongo.command_cursor import CommandCursor
from orcheo.graph.state import State
from orcheo.nodes.storage.mongodb.search import (
    _definitions_match,
    _extract_index_definition,
    _resolve_index_payload,
)
from orcheo.nodes.storage.mongodb import (
    MongoDBEnsureSearchIndexNode,
    MongoDBEnsureVectorIndexNode,
    MongoDBHybridSearchNode,
)


def _base_state() -> State:
    return State(messages=[], inputs={}, results={})


@pytest.mark.asyncio
async def test_ensure_search_index_creates_when_missing(mongo_context) -> None:
    mongo_context.collection.list_search_indexes.return_value = []

    node = MongoDBEnsureSearchIndexNode(
        name="ensure_search",
        database="test_db",
        collection="test_coll",
        definition={"mappings": {"dynamic": False}},
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "orcheo_test_coll_fts"}
    mongo_context.collection.create_search_index.assert_called_once_with(
        {
            "name": "orcheo_test_coll_fts",
            "definition": {"mappings": {"dynamic": False}},
        }
    )


@pytest.mark.asyncio
async def test_ensure_search_index_updates_on_mismatch(mongo_context) -> None:
    mongo_context.collection.list_search_indexes.return_value = [
        {
            "name": "orcheo_test_coll_fts",
            "definition": {"mappings": {"dynamic": True}},
        }
    ]

    node = MongoDBEnsureSearchIndexNode(
        name="ensure_search",
        database="test_db",
        collection="test_coll",
        definition={"mappings": {"dynamic": False}},
        mode="ensure_or_update",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "updated", "index_name": "orcheo_test_coll_fts"}
    mongo_context.collection.update_search_index.assert_called_once_with(
        "orcheo_test_coll_fts", {"mappings": {"dynamic": False}}
    )


@pytest.mark.asyncio
async def test_ensure_search_index_skips_in_ensure_mode(mongo_context) -> None:
    mongo_context.collection.list_search_indexes.return_value = [
        {
            "name": "orcheo_test_coll_fts",
            "definition": {"mappings": {"dynamic": True}},
        }
    ]

    node = MongoDBEnsureSearchIndexNode(
        name="ensure_search",
        database="test_db",
        collection="test_coll",
        definition={"mappings": {"dynamic": False}},
        mode="ensure",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "skipped", "index_name": "orcheo_test_coll_fts"}
    mongo_context.collection.update_search_index.assert_not_called()
    mongo_context.collection.create_search_index.assert_not_called()


@pytest.mark.asyncio
async def test_force_rebuild_drops_and_creates(mongo_context) -> None:
    mongo_context.collection.list_search_indexes.return_value = [
        {
            "name": "orcheo_test_coll_fts",
            "definition": {"mappings": {"dynamic": True}},
        }
    ]

    node = MongoDBEnsureSearchIndexNode(
        name="ensure_search",
        database="test_db",
        collection="test_coll",
        definition={"mappings": {"dynamic": False}},
        mode="force_rebuild",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "orcheo_test_coll_fts"}
    mongo_context.collection.drop_search_index.assert_called_once_with(
        "orcheo_test_coll_fts"
    )
    mongo_context.collection.create_search_index.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_search_index_uses_definition_name(mongo_context) -> None:
    mongo_context.collection.list_search_indexes.return_value = []

    node = MongoDBEnsureSearchIndexNode(
        name="ensure_search",
        database="test_db",
        collection="test_coll",
        definition={
            "name": "custom_search",
            "definition": {"mappings": {"dynamic": False}},
        },
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "custom_search"}
    mongo_context.collection.create_search_index.assert_called_once_with(
        {
            "name": "custom_search",
            "definition": {"mappings": {"dynamic": False}},
        }
    )


@pytest.mark.asyncio
async def test_vector_index_builds_definition_when_missing(mongo_context) -> None:
    mongo_context.collection.list_search_indexes.return_value = []

    node = MongoDBEnsureVectorIndexNode(
        name="ensure_vector",
        database="test_db",
        collection="test_coll",
        dimensions=3,
        similarity="cosine",
        path="embedding",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "orcheo_test_coll_vec"}
    mongo_context.collection.create_search_index.assert_called_once_with(
        {
            "name": "orcheo_test_coll_vec",
            "definition": {
                "mappings": {
                    "dynamic": False,
                    "fields": {
                        "embedding": {
                            "type": "vector",
                            "numDimensions": 3,
                            "similarity": "cosine",
                        }
                    },
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_ensure_vector_index_uses_definition_name(mongo_context) -> None:
    mongo_context.collection.list_search_indexes.return_value = []

    node = MongoDBEnsureVectorIndexNode(
        name="ensure_vector",
        database="test_db",
        collection="test_coll",
        definition={
            "name": "custom_vector",
            "definition": {"mappings": {"dynamic": False}},
        },
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "custom_vector"}
    mongo_context.collection.create_search_index.assert_called_once_with(
        {
            "name": "custom_vector",
            "definition": {"mappings": {"dynamic": False}},
        }
    )


def test_hybrid_search_builds_union_pipeline() -> None:
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        vector=[0.1, 0.2],
        text_paths=["body"],
        vector_path="embedding",
        top_k=3,
    )

    pipeline = node._build_pipeline()
    assert pipeline[0]["$search"]["text"]["query"] == "hello"

    union_stage = next(stage for stage in pipeline if "$unionWith" in stage)
    vector_pipeline = union_stage["$unionWith"]["pipeline"]
    assert vector_pipeline[0]["$vectorSearch"]["queryVector"] == [0.1, 0.2]

    group_stage = next(stage for stage in pipeline if "$group" in stage)
    assert group_stage["$group"]["score"] == {"$sum": "$rrf_score"}


def test_hybrid_search_builds_text_only_pipeline() -> None:
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        vector=None,
        text_paths=["body"],
        vector_path="embedding",
        top_k=3,
    )

    pipeline = node._build_pipeline()
    assert pipeline[0]["$search"]["text"]["query"] == "hello"
    assert all("$unionWith" not in stage for stage in pipeline)


def test_hybrid_search_builds_vector_only_pipeline() -> None:
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query=None,
        vector=[0.1, 0.2],
        text_paths=["body"],
        vector_path="embedding",
        top_k=3,
    )

    pipeline = node._build_pipeline()
    assert pipeline[0]["$vectorSearch"]["queryVector"] == [0.1, 0.2]
    assert all("$unionWith" not in stage for stage in pipeline)


@pytest.mark.asyncio
async def test_hybrid_search_normalizes_results() -> None:
    object_id = ObjectId("507f1f77bcf86cd799439011")
    cursor = MagicMock()
    cursor.to_list = AsyncMock(
        return_value=[{"_id": object_id, "score": 1.5, "title": "Doc"}]
    )
    collection = MagicMock()
    collection.aggregate.return_value = cursor

    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        vector=None,
        text_paths=["body"],
        vector_path="embedding",
        top_k=3,
    )
    node._async_collection = collection
    node._ensure_async_collection = MagicMock()

    result = await node.run(_base_state(), RunnableConfig())

    assert result["results"] == [
        {
            "id": str(object_id),
            "score": 1.5,
            "raw": {"_id": str(object_id), "title": "Doc"},
        }
    ]
    collection.aggregate.assert_called_once()
    cursor.to_list.assert_awaited_once_with(length=3)


@pytest.mark.asyncio
async def test_hybrid_search_uses_built_pipeline_for_run() -> None:
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    collection = MagicMock()
    collection.aggregate.return_value = cursor

    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        vector=[0.1, 0.2],
        text_paths=["body"],
        top_k=2,
    )
    node._async_collection = collection
    node._ensure_async_collection = MagicMock()

    await node.run(_base_state(), RunnableConfig())

    called_pipeline = collection.aggregate.call_args[0][0]
    assert called_pipeline[0]["$search"]["text"]["query"] == "hello"
    union_stage = next(stage for stage in called_pipeline if "$unionWith" in stage)
    vector_pipeline = union_stage["$unionWith"]["pipeline"]
    assert vector_pipeline[0]["$vectorSearch"]["queryVector"] == [0.1, 0.2]


def test_hybrid_search_requires_query_or_vector() -> None:
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query=None,
        vector=None,
        text_paths=["body"],
        vector_path="embedding",
    )

    with pytest.raises(ValueError, match="requires text_query or vector"):
        node._build_pipeline()


# --- Helper function tests (lines 29, 33, 41, 55->60) ---


def test_extract_index_definition_uses_latest_definition_key() -> None:
    """Line 29: return dict(definition) when 'latestDefinition' key exists."""
    index: dict[str, Any] = {"latestDefinition": {"mappings": {"dynamic": True}}}
    result = _extract_index_definition(index)
    assert result == {"mappings": {"dynamic": True}}


def test_extract_index_definition_uses_definition_key() -> None:
    """Return dict(definition) when 'definition' key exists."""
    index: dict[str, Any] = {"definition": {"mappings": {"dynamic": True}}}
    result = _extract_index_definition(index)
    assert result == {"mappings": {"dynamic": True}}


def test_extract_index_definition_returns_none_when_no_keys() -> None:
    """Line 33: return None when neither key exists."""
    result = _extract_index_definition({"name": "test"})
    assert result is None


def test_definitions_match_returns_false_when_existing_is_none() -> None:
    """Line 41: return False when existing_definition is None."""
    assert _definitions_match(None, {"mappings": {}}) is False


def test_resolve_index_payload_extracts_nested_name() -> None:
    """Lines 55->60: nested definition with name extraction."""
    definition: dict[str, Any] = {
        "name": "custom_name",
        "definition": {"mappings": {"dynamic": False}},
    }
    name, nested = _resolve_index_payload(definition, None)
    assert name == "custom_name"
    assert nested == {"mappings": {"dynamic": False}}


def test_resolve_index_payload_index_name_overrides() -> None:
    """Line 60: index_name overrides definition name."""
    definition: dict[str, Any] = {
        "name": "from_definition",
        "definition": {"mappings": {"dynamic": False}},
    }
    name, nested = _resolve_index_payload(definition, "override_name")
    assert name == "override_name"
    assert nested == {"mappings": {"dynamic": False}}


def test_resolve_index_payload_raises_when_no_name() -> None:
    """Line 63-65: raises when no name can be resolved."""
    with pytest.raises(ValueError, match="index_name is required"):
        _resolve_index_payload({"mappings": {}}, None)


# --- MongoDBEnsureSearchIndexNode tests ---


def test_ensure_search_index_empty_definition_raises() -> None:
    """Lines 95-96: empty definition raises ValueError."""
    with pytest.raises(ValueError, match="definition is required"):
        MongoDBEnsureSearchIndexNode(
            name="ensure_search",
            database="test_db",
            collection="test_coll",
            definition={},
        )


@pytest.mark.asyncio
async def test_ensure_search_index_with_explicit_index_name(
    mongo_context,
) -> None:
    """Line 101: _resolve_index_definition when self.index_name is set."""
    mongo_context.collection.list_search_indexes.return_value = []

    node = MongoDBEnsureSearchIndexNode(
        name="ensure_search",
        database="test_db",
        collection="test_coll",
        index_name="my_custom_index",
        definition={"mappings": {"dynamic": False}},
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "my_custom_index"}
    mongo_context.collection.create_search_index.assert_called_once_with(
        {
            "name": "my_custom_index",
            "definition": {"mappings": {"dynamic": False}},
        }
    )


@pytest.mark.asyncio
async def test_ensure_search_index_ensure_or_update_skips_when_matching(
    mongo_context,
) -> None:
    """Line 179: ensure_or_update returns skipped when definitions match."""
    definition: dict[str, Any] = {"mappings": {"dynamic": False}}
    mongo_context.collection.list_search_indexes.return_value = [
        {"name": "orcheo_test_coll_fts", "definition": definition}
    ]

    node = MongoDBEnsureSearchIndexNode(
        name="ensure_search",
        database="test_db",
        collection="test_coll",
        definition=definition,
        mode="ensure_or_update",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "skipped", "index_name": "orcheo_test_coll_fts"}
    mongo_context.collection.update_search_index.assert_not_called()


# --- MongoDBEnsureVectorIndexNode validation tests ---


def test_vector_index_requires_dimensions() -> None:
    """Lines 220-221: dimensions required when definition not provided."""
    with pytest.raises(ValueError, match="dimensions is required"):
        MongoDBEnsureVectorIndexNode(
            name="vec",
            database="db",
            collection="coll",
            similarity="cosine",
        )


def test_vector_index_requires_similarity() -> None:
    """Lines 223-224: similarity required when definition not provided."""
    with pytest.raises(ValueError, match="similarity is required"):
        MongoDBEnsureVectorIndexNode(
            name="vec",
            database="db",
            collection="coll",
            dimensions=128,
        )


def test_validate_dimensions_none() -> None:
    """Line 231: None passes through."""
    assert MongoDBEnsureVectorIndexNode._validate_dimensions(None) is None


def test_validate_dimensions_template_string() -> None:
    """Lines 233-234: template string passes through."""
    result = MongoDBEnsureVectorIndexNode._validate_dimensions("{{dims}}")
    assert result == "{{dims}}"


def test_validate_dimensions_string_integer() -> None:
    """Lines 236: string integer is converted."""
    result = MongoDBEnsureVectorIndexNode._validate_dimensions("128")
    assert result == 128


def test_validate_dimensions_invalid_string() -> None:
    """Lines 238-239: invalid string raises ValueError."""
    with pytest.raises(ValueError, match="dimensions must be an integer"):
        MongoDBEnsureVectorIndexNode._validate_dimensions("bad")


def test_validate_dimensions_negative() -> None:
    """Lines 241-242: negative value raises ValueError."""
    with pytest.raises(ValueError, match="dimensions must be > 0"):
        MongoDBEnsureVectorIndexNode._validate_dimensions(-5)


def test_validate_dimensions_zero() -> None:
    """Lines 241-242: zero raises ValueError."""
    with pytest.raises(ValueError, match="dimensions must be > 0"):
        MongoDBEnsureVectorIndexNode._validate_dimensions(0)


def test_build_default_definition_missing_dims_or_similarity() -> None:
    """Lines 247-248: raises when dimensions or similarity is None."""
    node = MongoDBEnsureVectorIndexNode(
        name="vec",
        database="db",
        collection="coll",
        definition={"mappings": {}},
    )
    node.dimensions = None
    node.similarity = None
    with pytest.raises(
        ValueError, match="definition, dimensions, and similarity must be provided"
    ):
        node._build_default_definition()


def test_build_default_definition_str_dimensions_raises() -> None:
    """Lines 250-251: raises when dimensions is unresolved string."""
    node = MongoDBEnsureVectorIndexNode(
        name="vec",
        database="db",
        collection="coll",
        dimensions="{{dims}}",
        similarity="cosine",
    )
    with pytest.raises(
        ValueError, match="dimensions must resolve to an integer before execution"
    ):
        node._build_default_definition()


@pytest.mark.asyncio
async def test_vector_index_with_explicit_index_name(mongo_context) -> None:
    """Line 268: _resolve_index_definition when index_name is set."""
    mongo_context.collection.list_search_indexes.return_value = []

    node = MongoDBEnsureVectorIndexNode(
        name="vec",
        database="test_db",
        collection="test_coll",
        index_name="my_vec_index",
        dimensions=3,
        similarity="cosine",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "my_vec_index"}


@pytest.mark.asyncio
async def test_vector_index_force_rebuild_drops_and_creates(mongo_context) -> None:
    """Lines 311-322: force_rebuild in vector index node."""
    mongo_context.collection.list_search_indexes.return_value = [
        {
            "name": "orcheo_test_coll_vec",
            "definition": {"mappings": {"dynamic": True}},
        }
    ]

    node = MongoDBEnsureVectorIndexNode(
        name="vec",
        database="test_db",
        collection="test_coll",
        dimensions=3,
        similarity="cosine",
        mode="force_rebuild",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "orcheo_test_coll_vec"}
    mongo_context.collection.drop_search_index.assert_called_once_with(
        "orcheo_test_coll_vec"
    )
    mongo_context.collection.create_search_index.assert_called_once()


@pytest.mark.asyncio
async def test_vector_index_force_rebuild_without_existing(mongo_context) -> None:
    """Lines 311-322: force_rebuild with no existing index."""
    mongo_context.collection.list_search_indexes.return_value = []

    node = MongoDBEnsureVectorIndexNode(
        name="vec",
        database="test_db",
        collection="test_coll",
        dimensions=3,
        similarity="cosine",
        mode="force_rebuild",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "orcheo_test_coll_vec"}
    mongo_context.collection.drop_search_index.assert_not_called()
    mongo_context.collection.create_search_index.assert_called_once()


@pytest.mark.asyncio
async def test_vector_index_ensure_mode_skips_existing(mongo_context) -> None:
    """Lines 333-334: ensure mode returns skipped for existing index."""
    mongo_context.collection.list_search_indexes.return_value = [
        {"name": "orcheo_test_coll_vec", "definition": {"mappings": {}}}
    ]

    node = MongoDBEnsureVectorIndexNode(
        name="vec",
        database="test_db",
        collection="test_coll",
        dimensions=3,
        similarity="cosine",
        mode="ensure",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "skipped", "index_name": "orcheo_test_coll_vec"}
    mongo_context.collection.create_search_index.assert_not_called()
    mongo_context.collection.update_search_index.assert_not_called()


@pytest.mark.asyncio
async def test_vector_index_ensure_or_update_updates_on_mismatch(
    mongo_context,
) -> None:
    """Lines 337-344: ensure_or_update updates when definitions differ."""
    mongo_context.collection.list_search_indexes.return_value = [
        {
            "name": "orcheo_test_coll_vec",
            "definition": {"mappings": {"dynamic": True}},
        }
    ]

    node = MongoDBEnsureVectorIndexNode(
        name="vec",
        database="test_db",
        collection="test_coll",
        dimensions=3,
        similarity="cosine",
        mode="ensure_or_update",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "updated", "index_name": "orcheo_test_coll_vec"}
    mongo_context.collection.update_search_index.assert_called_once()


@pytest.mark.asyncio
async def test_vector_index_ensure_or_update_skips_when_matching(
    mongo_context,
) -> None:
    """Lines 346: ensure_or_update returns skipped when matching."""
    definition: dict[str, Any] = {
        "mappings": {
            "dynamic": False,
            "fields": {
                "embedding": {
                    "type": "vector",
                    "numDimensions": 3,
                    "similarity": "cosine",
                }
            },
        }
    }
    mongo_context.collection.list_search_indexes.return_value = [
        {"name": "orcheo_test_coll_vec", "definition": definition}
    ]

    node = MongoDBEnsureVectorIndexNode(
        name="vec",
        database="test_db",
        collection="test_coll",
        dimensions=3,
        similarity="cosine",
        mode="ensure_or_update",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "skipped", "index_name": "orcheo_test_coll_vec"}
    mongo_context.collection.update_search_index.assert_not_called()


# --- MongoDBHybridSearchNode tests ---


def test_hybrid_search_text_paths_template_passes_validation() -> None:
    """Line 407: template text_paths passes through validator."""
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        text_paths="{{paths}}",
    )
    assert node.text_paths == "{{paths}}"


def test_hybrid_search_vector_template_passes_validation() -> None:
    """Line 400: template vector passes through validator."""
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        vector="{{embedding}}",
        text_paths=["body"],
    )
    assert node.vector == "{{embedding}}"


def test_build_text_pipeline_raises_when_text_paths_is_str() -> None:
    """Lines 418, 420-421: raises when text_paths is unresolved string."""
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        text_paths="{{paths}}",
    )
    with pytest.raises(
        ValueError, match="text_paths must resolve to a list before execution"
    ):
        node._build_text_pipeline(include_rrf=False)


def test_build_vector_pipeline_raises_when_vector_is_str() -> None:
    """Lines 458, 460-461: raises when vector is unresolved string."""
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        vector="{{embedding}}",
        text_paths=["body"],
    )
    with pytest.raises(
        ValueError, match="vector must resolve to a list before execution"
    ):
        node._build_vector_pipeline(include_rrf=False)


def test_build_text_pipeline_includes_filter() -> None:
    """Line 431: text pipeline appends $match when filter is set."""
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        text_paths=["body"],
        filter={"status": "active"},
    )
    pipeline = node._build_text_pipeline(include_rrf=False)
    match_stages = [s for s in pipeline if "$match" in s]
    assert len(match_stages) == 1
    assert match_stages[0]["$match"] == {"status": "active"}


def test_build_vector_pipeline_includes_filter() -> None:
    """Line 474: vector pipeline appends $match when filter is set."""
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        vector=[0.1, 0.2],
        text_paths=["body"],
        filter={"status": "active"},
    )
    pipeline = node._build_vector_pipeline(include_rrf=False)
    match_stages = [s for s in pipeline if "$match" in s]
    assert len(match_stages) == 1
    assert match_stages[0]["$match"] == {"status": "active"}


def test_normalize_results_rrf_format() -> None:
    """Lines 541-548: normalize results with raw+score (RRF output)."""
    object_id = ObjectId("507f1f77bcf86cd799439011")
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        text_paths=["body"],
    )
    items: list[dict[str, Any]] = [
        {
            "_id": str(object_id),
            "score": 2.5,
            "raw": {"_id": object_id, "title": "Doc 1"},
        }
    ]
    result = node._normalize_results(items)
    assert len(result) == 1
    assert result[0]["id"] == str(object_id)
    assert result[0]["score"] == 2.5
    assert result[0]["raw"]["title"] == "Doc 1"


def test_normalize_results_rrf_non_dict_raw() -> None:
    """Lines 547-548: normalize results where raw is not a dict."""
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        text_paths=["body"],
    )
    items: list[dict[str, Any]] = [{"_id": "id1", "score": 1.0, "raw": "not-a-dict"}]
    result = node._normalize_results(items)
    assert result[0]["id"] == "id1"
    assert result[0]["score"] == 1.0
    assert result[0]["raw"] == "not-a-dict"


def test_normalize_results_score_fallback() -> None:
    """Line 553: falls back to _score when score is absent."""
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        text_paths=["body"],
    )
    items: list[dict[str, Any]] = [{"_id": "id1", "_score": 3.14, "title": "Doc"}]
    result = node._normalize_results(items)
    assert result[0]["score"] == pytest.approx(3.14)
    assert "_score" not in result[0]["raw"]


def test_build_text_pipeline_returns_empty_when_no_text_query() -> None:
    """Line 418: returns [] when text_query is None."""
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query=None,
        vector=[0.1],
        text_paths=["body"],
    )
    assert node._build_text_pipeline(include_rrf=False) == []


def test_build_vector_pipeline_returns_empty_when_no_vector() -> None:
    """Line 458: returns [] when vector is None."""
    node = MongoDBHybridSearchNode(
        name="hybrid",
        database="test_db",
        collection="test_coll",
        text_query="hello",
        vector=None,
        text_paths=["body"],
    )
    assert node._build_vector_pipeline(include_rrf=False) == []


@pytest.mark.asyncio
async def test_search_index_list_with_command_cursor(mongo_context) -> None:
    """Line 113: _list_search_indexes when result is a CommandCursor."""
    cursor = MagicMock(spec=CommandCursor)
    cursor.__iter__ = MagicMock(
        return_value=iter(
            [{"name": "orcheo_test_coll_fts", "definition": {"mappings": {}}}]
        )
    )
    mongo_context.collection.list_search_indexes.return_value = cursor

    node = MongoDBEnsureSearchIndexNode(
        name="ensure_search",
        database="test_db",
        collection="test_coll",
        definition={"mappings": {"dynamic": False}},
        mode="ensure",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "skipped", "index_name": "orcheo_test_coll_fts"}


@pytest.mark.asyncio
async def test_vector_index_list_with_command_cursor(mongo_context) -> None:
    """Line 280: _list_search_indexes in vector node with CommandCursor."""
    cursor = MagicMock(spec=CommandCursor)
    cursor.__iter__ = MagicMock(
        return_value=iter(
            [{"name": "orcheo_test_coll_vec", "definition": {"mappings": {}}}]
        )
    )
    mongo_context.collection.list_search_indexes.return_value = cursor

    node = MongoDBEnsureVectorIndexNode(
        name="vec",
        database="test_db",
        collection="test_coll",
        dimensions=3,
        similarity="cosine",
        mode="ensure",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "skipped", "index_name": "orcheo_test_coll_vec"}


def test_resolve_index_payload_nested_definition_without_name() -> None:
    """Branch 55->60: nested definition present but name is missing."""
    definition: dict[str, Any] = {
        "definition": {"mappings": {"dynamic": False}},
    }
    name, nested = _resolve_index_payload(definition, "fallback_name")
    assert name == "fallback_name"
    assert nested == {"mappings": {"dynamic": False}}


def test_resolve_index_payload_nested_definition_with_empty_name() -> None:
    """Branch 55->60: nested definition with empty string name."""
    definition: dict[str, Any] = {
        "name": "",
        "definition": {"mappings": {"dynamic": False}},
    }
    name, nested = _resolve_index_payload(definition, "fallback")
    assert name == "fallback"


@pytest.mark.asyncio
async def test_search_index_find_index_no_match(mongo_context) -> None:
    """Branch 120->119: _find_index loops but finds no match."""
    mongo_context.collection.list_search_indexes.return_value = [
        {"name": "other_index", "definition": {"mappings": {}}}
    ]

    node = MongoDBEnsureSearchIndexNode(
        name="ensure_search",
        database="test_db",
        collection="test_coll",
        definition={"mappings": {"dynamic": False}},
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "orcheo_test_coll_fts"}


@pytest.mark.asyncio
async def test_search_index_force_rebuild_without_existing(mongo_context) -> None:
    """Branch 144->149: force_rebuild when existing is None."""
    mongo_context.collection.list_search_indexes.return_value = []

    node = MongoDBEnsureSearchIndexNode(
        name="ensure_search",
        database="test_db",
        collection="test_coll",
        definition={"mappings": {"dynamic": False}},
        mode="force_rebuild",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "orcheo_test_coll_fts"}
    mongo_context.collection.drop_search_index.assert_not_called()
    mongo_context.collection.create_search_index.assert_called_once()


@pytest.mark.asyncio
async def test_vector_index_find_index_no_match(mongo_context) -> None:
    """Branch 287->286: _find_index loops without match in vector node."""
    mongo_context.collection.list_search_indexes.return_value = [
        {"name": "other_index", "definition": {"mappings": {}}}
    ]

    node = MongoDBEnsureVectorIndexNode(
        name="vec",
        database="test_db",
        collection="test_coll",
        dimensions=3,
        similarity="cosine",
    )

    result = await node.run(_base_state(), RunnableConfig())

    assert result == {"status": "created", "index_name": "orcheo_test_coll_vec"}
