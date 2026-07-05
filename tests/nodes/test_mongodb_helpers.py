"""Additional MongoDBNode helper and error-handling tests."""

from __future__ import annotations
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch
import pytest
from bson import ObjectId
from langchain_core.runnables import RunnableConfig
from pymongo.errors import (
    AutoReconnect,
    ConfigurationError,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)
from orcheo.graph.state import State
from orcheo.nodes.storage.mongodb import (
    MongoDBHybridSearchNode,
    MongoDBNode,
    MongoDBUpdateManyNode,
)


def _build_node(*, operation: str, **overrides: Any) -> MongoDBNode:
    """Create a MongoDBNode with sensible defaults for helper tests."""

    base_kwargs = {
        "name": "helper_node",
        "connection_string": "mongodb://helper",
        "database": "test_db",
        "collection": "test_collection",
        "operation": operation,
    }
    base_kwargs.update(overrides)
    return MongoDBNode(**base_kwargs)


def _build_update_many() -> MongoDBUpdateManyNode:
    """Create a MongoDBUpdateManyNode with sane defaults."""

    return MongoDBUpdateManyNode(
        name="update_many",
        database="test_db",
        collection="test_collection",
        filter={"match": True},
        update={"$set": {"match": True}},
    )


def _build_hybrid_search(**overrides: Any) -> MongoDBHybridSearchNode:
    """Create a MongoDBHybridSearchNode with sane defaults."""

    base_kwargs = {
        "name": "hybrid",
        "connection_string": "mongodb://helper",
        "database": "test_db",
        "collection": "test_collection",
        "text_query": "hello",
        "text_paths": ["body"],
    }
    base_kwargs.update(overrides)
    return MongoDBHybridSearchNode(**base_kwargs)


def test_limit_validator_accepts_templates_and_numbers() -> None:
    assert MongoDBNode._validate_limit(None) is None
    assert MongoDBNode._validate_limit("{{limit}}") == "{{limit}}"
    assert MongoDBNode._validate_limit("10") == 10


def test_limit_validator_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="limit must be an integer"):
        MongoDBNode._validate_limit("bad")
    with pytest.raises(ValueError, match="limit must be >= 0"):
        MongoDBNode._validate_limit(-1)


def test_resolve_filter_prefers_explicit_filter() -> None:
    node = _build_node(operation="find", filter={"status": "ok"})
    assert node._resolve_filter() == {"status": "ok"}

    node.filter = None
    node.query = [{"status": "list"}]
    assert node._resolve_filter() == {}


def test_resolve_filter_rejects_unresolved_template() -> None:
    node = _build_node(operation="find", filter="{{prepare_event.filter}}")

    with pytest.raises(
        ValueError, match="filter must resolve to a dict before execution"
    ):
        node._resolve_filter()


def test_resolve_update_handles_explicit_and_query_updates() -> None:
    node = _build_node(operation="update_one", update={"$set": {"count": 1}})
    assert node._resolve_update() == {"$set": {"count": 1}}

    node.update = None
    with pytest.raises(ValueError, match="update is required for update operations"):
        node._resolve_update()


def test_resolve_update_rejects_unresolved_template() -> None:
    node = _build_node(operation="update_one", update="{{prepare_event.update}}")

    with pytest.raises(
        ValueError, match="update must resolve to a dict before execution"
    ):
        node._resolve_update()


def test_resolve_pipeline_handles_variants_and_errors() -> None:
    node = _build_node(
        operation="aggregate",
        pipeline=[{"$match": {"value": 1}}],
    )
    assert node._resolve_pipeline() == [{"$match": {"value": 1}}]

    node.pipeline = None
    node.query = [{"$match": {"value": 2}}]
    assert node._resolve_pipeline() == [{"$match": {"value": 2}}]
    node.query = {"foo": "bar"}
    with pytest.raises(
        ValueError, match="pipeline is required for aggregate operations"
    ):
        node._resolve_pipeline()


def test_resolve_projection_handles_dict_list_and_templates() -> None:
    node = _build_node(operation="find", projection={"title": 1, "content": 1})
    assert node._resolve_projection() == {"title": 1, "content": 1}

    node.projection = ["title", "content"]
    assert node._resolve_projection() == ["title", "content"]

    node.projection = "{{prepare_event.projection}}"
    with pytest.raises(ValueError, match="projection must resolve to a dict or list"):
        node._resolve_projection()


def test_normalize_sort_converts_dicts_and_lists() -> None:
    node = _build_node(operation="find")
    assert node._normalize_sort({"name": -1}) == [("name", -1)]
    assert node._normalize_sort([("name", -1)]) == [("name", -1)]


def test_build_operation_call_covers_each_operation_type() -> None:
    object_id = ObjectId("507f1f77bcf86cd799439011")

    aggregate_node = _build_node(
        operation="aggregate",
        pipeline=[{"$match": {"_id": str(object_id)}}],
    )
    aggregate_args, aggregate_kwargs = aggregate_node._build_operation_call()
    assert aggregate_kwargs == {}
    assert aggregate_args[0][0]["$match"]["_id"] == object_id

    update_node = _build_node(
        operation="update_one",
        filter={"_id": str(object_id)},
        update={"$set": {"value": 1}},
    )
    update_args, update_kwargs = update_node._build_operation_call()
    assert update_kwargs == {}
    assert update_args[0]["_id"] == object_id
    assert update_args[1] == {"$set": {"value": 1}}

    find_node = _build_node(
        operation="find",
        filter={"status": "active"},
        projection={"_id": 1, "status": 1},
        sort={"isoDate": -1},
        limit=5,
    )
    find_args, find_kwargs = find_node._build_operation_call()
    assert find_kwargs["projection"] == {"_id": 1, "status": 1}
    assert find_kwargs["sort"] == [("isoDate", -1)]
    assert find_kwargs["limit"] == 5
    assert find_args[0] == {"status": "active"}

    other_node = _build_node(operation="create_index", query={"name": 1})
    other_args, other_kwargs = other_node._build_operation_call()
    assert other_args == [{"name": 1}]
    assert other_kwargs == {}


def test_build_operation_call_applies_projection_to_find_one_and_update() -> None:
    node = _build_node(
        operation="find_one_and_update",
        filter={"status": "active"},
        update={"$set": {"status": "done"}},
        projection={"status": 1},
    )

    args, kwargs = node._build_operation_call()

    assert args == [{"status": "active"}, {"$set": {"status": "done"}}]
    assert kwargs["projection"] == {"status": 1}


def test_resolve_limit_requires_integer_values() -> None:
    node = _build_node(operation="find", limit="{{limit}}")
    with pytest.raises(
        ValueError, match="limit must resolve to an integer before execution"
    ):
        node._resolve_limit()

    node.limit = "bad"
    with pytest.raises(ValueError, match="limit must be an integer"):
        node._resolve_limit()

    node.limit = None
    with pytest.raises(ValueError, match="limit is not set for find operations"):
        node._resolve_limit()


def test_coerce_object_ids_converts_nested_ids() -> None:
    object_id = ObjectId("507f1f77bcf86cd799439011")
    payload = {
        "_id": str(object_id),
        "nested": [{"_id": str(object_id)}],
        "other": {"_id": "not-an-id"},
    }

    coerced = MongoDBNode._coerce_object_ids(payload)
    assert coerced["_id"] == object_id
    assert coerced["nested"][0]["_id"] == object_id
    assert coerced["other"]["_id"] == "not-an-id"


def test_encode_bson_normalizes_datetime_and_binary_values() -> None:
    created_at = datetime(2026, 3, 23, 15, 30, tzinfo=UTC)
    payload = {
        "created_at": created_at,
        "nested": {"_id": ObjectId("507f1f77bcf86cd799439011")},
        "blob": b"abc",
    }

    encoded = MongoDBNode._encode_bson(payload)

    assert encoded["created_at"] == created_at.isoformat()
    assert encoded["nested"]["_id"] == "507f1f77bcf86cd799439011"
    assert encoded["blob"] == "YWJj"


def test_shared_client_lifecycle() -> None:
    MongoDBNode._client_cache.clear()
    MongoDBNode._client_ref_counts.clear()
    client_mock = MagicMock()

    with patch(
        "orcheo.nodes.storage.mongodb.base.MongoClient",
        return_value=client_mock,
    ):
        first = MongoDBNode._get_shared_client("conn")
        second = MongoDBNode._get_shared_client("conn")
        assert first is client_mock
        assert second is client_mock
        assert MongoDBNode._client_ref_counts["conn"] == 2

    MongoDBNode._release_shared_client("conn")
    assert MongoDBNode._client_ref_counts["conn"] == 1
    client_mock.close.assert_not_called()

    MongoDBNode._release_shared_client("conn")
    assert "conn" not in MongoDBNode._client_ref_counts
    client_mock.close.assert_called_once()

    client_one = MagicMock()
    client_two = MagicMock()
    MongoDBNode._client_cache.clear()
    MongoDBNode._client_ref_counts.clear()

    with patch(
        "orcheo.nodes.storage.mongodb.base.MongoClient",
        side_effect=[client_one, client_two],
    ):
        MongoDBNode._get_shared_client("conn-one")
        MongoDBNode._get_shared_client("conn-two")

    MongoDBNode._close_all_clients()
    client_one.close.assert_called_once()
    client_two.close.assert_called_once()


def test_release_shared_client_ignores_unknown_connection() -> None:
    MongoDBNode._client_cache.clear()
    MongoDBNode._client_ref_counts.clear()

    MongoDBNode._release_shared_client("missing")
    assert MongoDBNode._client_cache == {}
    assert MongoDBNode._client_ref_counts == {}


def test_shared_async_client_lifecycle() -> None:
    MongoDBHybridSearchNode._async_client_cache.clear()
    MongoDBHybridSearchNode._async_client_ref_counts.clear()
    client_mock = MagicMock()

    with patch(
        "orcheo.nodes.storage.mongodb.base.AsyncIOMotorClient",
        return_value=client_mock,
    ):
        first = MongoDBHybridSearchNode._get_shared_async_client("conn")
        second = MongoDBHybridSearchNode._get_shared_async_client("conn")
        assert first is client_mock
        assert second is client_mock
        assert MongoDBHybridSearchNode._async_client_ref_counts["conn"] == 2

    MongoDBHybridSearchNode._release_shared_async_client("conn")
    assert MongoDBHybridSearchNode._async_client_ref_counts["conn"] == 1
    client_mock.close.assert_not_called()

    MongoDBHybridSearchNode._release_shared_async_client("conn")
    assert "conn" not in MongoDBHybridSearchNode._async_client_ref_counts
    client_mock.close.assert_called_once()


def test_release_shared_async_client_ignores_unknown_connection() -> None:
    MongoDBHybridSearchNode._async_client_cache.clear()
    MongoDBHybridSearchNode._async_client_ref_counts.clear()

    MongoDBHybridSearchNode._release_shared_async_client("missing")
    assert MongoDBHybridSearchNode._async_client_cache == {}
    assert MongoDBHybridSearchNode._async_client_ref_counts == {}


def test_close_all_async_clients_closes_and_clears_cache() -> None:
    first_client = MagicMock()
    second_client = MagicMock()
    MongoDBHybridSearchNode._async_client_cache.clear()
    MongoDBHybridSearchNode._async_client_ref_counts.clear()
    MongoDBHybridSearchNode._async_client_cache["conn-a"] = first_client
    MongoDBHybridSearchNode._async_client_cache["conn-b"] = second_client
    MongoDBHybridSearchNode._async_client_ref_counts["conn-a"] = 1
    MongoDBHybridSearchNode._async_client_ref_counts["conn-b"] = 2

    MongoDBHybridSearchNode._close_all_async_clients()

    first_client.close.assert_called_once()
    second_client.close.assert_called_once()
    assert MongoDBHybridSearchNode._async_client_cache == {}
    assert MongoDBHybridSearchNode._async_client_ref_counts == {}


def test_ensure_collection_raises_when_client_missing() -> None:
    node = _build_node(operation="find")
    with patch.object(MongoDBNode, "_get_shared_client", return_value=None):
        with pytest.raises(
            RuntimeError, match="MongoDB client could not be initialized"
        ):
            node._ensure_collection()


def test_ensure_collection_switches_clients_on_connection_change() -> None:
    node = _build_node(operation="find")
    node._client = MagicMock()
    node._client_key = "old-connection"
    node.connection_string = "new-connection"

    replacement_db = MagicMock()
    replacement_collection = MagicMock()
    replacement_db.__getitem__.return_value = replacement_collection

    replacement_client = MagicMock()
    replacement_client.__getitem__.return_value = replacement_db

    with patch.object(
        MongoDBNode, "_get_shared_client", return_value=replacement_client
    ) as get_client:
        with patch.object(MongoDBNode, "_release_client") as release_client:
            node._ensure_collection()

    release_client.assert_called_once()
    get_client.assert_called_once_with("new-connection")
    assert node._client is replacement_client
    assert node._client_key == "new-connection"
    assert node._collection is replacement_collection


def test_ensure_async_collection_switches_clients_on_connection_change() -> None:
    node = _build_hybrid_search()
    node._async_client = MagicMock()
    node._async_client_key = "old-connection"
    node.connection_string = "new-connection"

    replacement_db = MagicMock()
    replacement_collection = MagicMock()
    replacement_db.__getitem__.return_value = replacement_collection

    replacement_client = MagicMock()
    replacement_client.__getitem__.return_value = replacement_db

    with patch.object(
        MongoDBHybridSearchNode,
        "_get_shared_async_client",
        return_value=replacement_client,
    ) as get_client:
        with patch.object(
            MongoDBHybridSearchNode, "_release_async_client"
        ) as release_client:
            node._ensure_async_collection()

    release_client.assert_called_once()
    get_client.assert_called_once_with("new-connection")
    assert node._async_client is replacement_client
    assert node._async_client_key == "new-connection"
    assert node._async_collection is replacement_collection


def test_ensure_async_collection_initializes_client_when_missing() -> None:
    node = _build_hybrid_search(connection_string="new-connection")

    replacement_db = MagicMock()
    replacement_collection = MagicMock()
    replacement_db.__getitem__.return_value = replacement_collection
    replacement_client = MagicMock()
    replacement_client.__getitem__.return_value = replacement_db

    with patch.object(
        MongoDBHybridSearchNode,
        "_get_shared_async_client",
        return_value=replacement_client,
    ) as get_client:
        node._ensure_async_collection()

    get_client.assert_called_once_with("new-connection")
    assert node._async_client is replacement_client
    assert node._async_client_key == "new-connection"
    assert node._async_collection is replacement_collection


def test_ensure_async_collection_reuses_existing_client_without_switch() -> None:
    node = _build_hybrid_search(connection_string="same-connection")
    existing_db = MagicMock()
    existing_collection = MagicMock()
    existing_db.__getitem__.return_value = existing_collection
    existing_client = MagicMock()
    existing_client.__getitem__.return_value = existing_db
    node._async_client = existing_client
    node._async_client_key = "same-connection"

    with patch.object(
        MongoDBHybridSearchNode, "_get_shared_async_client"
    ) as get_client:
        with patch.object(
            MongoDBHybridSearchNode, "_release_async_client"
        ) as release_client:
            node._ensure_async_collection()

    get_client.assert_not_called()
    release_client.assert_not_called()
    assert node._async_client is existing_client
    assert node._async_collection is existing_collection


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception, message",
    [
        (AutoReconnect("test"), "MongoDB network error"),
        (OperationFailure("auth", 13), "MongoDB authentication/authorization error"),
        (OperationFailure("other", 1), "MongoDB operation error"),
        (ConfigurationError("bad"), "MongoDB configuration error"),
        (PyMongoError("generic"), "MongoDB error during"),
        (
            ServerSelectionTimeoutError("timeout"),
            "MongoDB network error",
        ),
    ],
)
async def test_run_translates_pymongo_errors(
    mongo_context,
    exception,
    message,
) -> None:
    node = mongo_context.build_node(operation="find")
    state = State(messages=[], inputs={}, node_results={})

    mongo_context.collection.find.side_effect = exception

    with pytest.raises(RuntimeError, match=message):
        await node.run(state, RunnableConfig())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception, message",
    [
        (AutoReconnect("test"), "MongoDB network error"),
        (OperationFailure("auth", 13), "MongoDB authentication/authorization error"),
        (OperationFailure("other", 1), "MongoDB operation error"),
        (ConfigurationError("bad"), "MongoDB configuration error"),
        (PyMongoError("generic"), "MongoDB error during"),
        (
            ServerSelectionTimeoutError("timeout"),
            "MongoDB network error",
        ),
    ],
)
async def test_execute_async_operation_translates_pymongo_errors(
    exception: Exception,
    message: str,
) -> None:
    node = _build_hybrid_search()

    async def _operation() -> Any:
        raise exception

    with pytest.raises(RuntimeError, match=message):
        await node._execute_async_operation(
            context="hybrid search", operation=_operation
        )


def test_update_many_requires_filter_and_update() -> None:
    node = _build_update_many()
    node.filter = None
    with pytest.raises(
        ValueError, match="filter is required for update_many operations"
    ):
        node._resolve_filter()

    node = _build_update_many()
    node.update = None
    with pytest.raises(
        ValueError, match="update is required for update_many operations"
    ):
        node._resolve_update()

    node = _build_update_many()
    node.update = {}
    with pytest.raises(
        ValueError, match="update must not be empty for update_many operations"
    ):
        node._resolve_update()
