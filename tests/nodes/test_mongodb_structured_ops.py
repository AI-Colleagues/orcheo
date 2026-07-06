"""MongoDBNode structured operation tests."""

from __future__ import annotations
from datetime import UTC, datetime
from unittest.mock import Mock
import pytest
from bson import ObjectId
from langchain_core.runnables import RunnableConfig
from pymongo.results import UpdateResult
from orcheo.graph.state import State
from orcheo.nodes.storage.mongodb import (
    MongoDBAggregateNode,
    MongoDBFindNode,
    MongoDBUpdateManyNode,
)


@pytest.mark.asyncio
async def test_mongodb_aggregate_uses_pipeline(mongo_context) -> None:
    mongo_context.collection.aggregate.return_value = [
        {"unread_count": 5},
    ]

    node = MongoDBAggregateNode(
        name="aggregate_node",
        database="test_db",
        collection="rss_feeds",
        pipeline=[{"$match": {"read": False}}, {"$count": "unread_count"}],
    )

    state = State(messages=[], inputs={}, node_results={})
    result = await node.run(state, RunnableConfig())

    mongo_context.collection.aggregate.assert_called_once_with(
        [{"$match": {"read": False}}, {"$count": "unread_count"}]
    )
    assert result["data"] == [{"unread_count": 5}]


@pytest.mark.asyncio
async def test_mongodb_find_uses_sort_and_limit(mongo_context) -> None:
    created_at = datetime(2026, 3, 23, 8, 15, tzinfo=UTC)
    mongo_context.collection.find.return_value = [
        {"_id": "1", "created_at": created_at}
    ]

    node = MongoDBFindNode(
        name="find_node",
        database="test_db",
        collection="rss_feeds",
        filter={"read": False},
        projection={"_id": 1, "created_at": 1},
        sort={"isoDate": -1},
        limit=30,
    )

    state = State(messages=[], inputs={}, node_results={})
    result = await node.run(state, RunnableConfig())

    mongo_context.collection.find.assert_called_once_with(
        {"read": False},
        projection={"_id": 1, "created_at": 1},
        sort=[("isoDate", -1)],
        limit=30,
    )
    assert result["data"] == [{"_id": "1", "created_at": created_at.isoformat()}]


def test_mongodb_find_allows_templated_limit() -> None:
    node = MongoDBFindNode(
        name="find_node",
        database="test_db",
        collection="rss_feeds",
        filter={"read": False},
        sort={"isoDate": -1},
        limit="{{config.configurable.item_limit}}",
    )

    assert node.limit == "{{config.configurable.item_limit}}"


@pytest.mark.asyncio
async def test_mongodb_update_many_uses_filter_and_update(mongo_context) -> None:
    mock_result = Mock(spec=UpdateResult)
    mock_result.matched_count = 1
    mock_result.modified_count = 1
    mock_result.upserted_id = None
    mock_result.acknowledged = True
    mongo_context.collection.update_many.return_value = mock_result

    node = MongoDBUpdateManyNode(
        name="update_node",
        database="test_db",
        collection="rss_feeds",
        filter={"_id": {"$in": ["1", "2"]}},
        update={"$set": {"read": True}},
    )

    state = State(messages=[], inputs={}, node_results={})
    result = await node.run(state, RunnableConfig())

    mongo_context.collection.update_many.assert_called_once_with(
        {"_id": {"$in": ["1", "2"]}},
        {"$set": {"read": True}},
    )
    assert result["data"]["matched_count"] == 1
    assert result["data"]["modified_count"] == 1


@pytest.mark.asyncio
async def test_mongodb_update_many_coerces_object_ids(
    mongo_context,
) -> None:
    mock_result = Mock(spec=UpdateResult)
    mock_result.matched_count = 1
    mock_result.modified_count = 1
    mock_result.upserted_id = None
    mock_result.acknowledged = True
    mongo_context.collection.update_many.return_value = mock_result

    object_id = ObjectId("507f1f77bcf86cd799439011")
    node = MongoDBUpdateManyNode(
        name="update_node",
        database="test_db",
        collection="rss_feeds",
        filter={"_id": {"$in": [str(object_id)]}},
        update={"$set": {"read": True}},
    )

    state = State(messages=[], inputs={}, node_results={})
    await node.run(state, RunnableConfig())

    mongo_context.collection.update_many.assert_called_once_with(
        {"_id": {"$in": [object_id]}},
        {"$set": {"read": True}},
    )


@pytest.mark.asyncio
async def test_mongodb_update_many_rejects_empty_update(
    mongo_context,
) -> None:
    node = MongoDBUpdateManyNode(
        name="update_node",
        database="test_db",
        collection="rss_feeds",
        filter={"read": False},
        update={},
    )

    state = State(messages=[], inputs={}, node_results={})
    with pytest.raises(ValueError, match="update must not be empty"):
        await node.run(state, RunnableConfig())
