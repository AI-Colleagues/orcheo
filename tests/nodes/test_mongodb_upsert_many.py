"""Tests for MongoDBUpsertManyNode."""

from __future__ import annotations
from typing import Any
from unittest.mock import Mock
import pytest
from langchain_core.runnables import RunnableConfig
from pymongo.results import BulkWriteResult
from orcheo.graph.state import State
from orcheo.nodes.integrations.databases.mongodb import MongoDBUpsertManyNode
from tests.nodes.conftest import MongoTestContext


_RECORDS = [
    {
        "link": "https://example.com/1",
        "title": "One",
        "read": True,
        "fetched_at": "2026-02-28T09:00:00+00:00",
    },
    {
        "link": "https://example.com/2",
        "title": "Two",
        "read": True,
        "fetched_at": "2026-02-28T09:30:00+00:00",
    },
]


def _base_state() -> State:
    return State(messages=[], inputs={}, results={})


def _state_with_records(
    *,
    source_key: str = "fetch_rss",
    records_field: str = "documents",
    records: list[dict[str, Any]] | None = None,
    source_as_list: bool = False,
) -> State:
    payload = list(records) if records is not None else list(_RECORDS)
    if source_as_list:
        results: dict[str, Any] = {source_key: payload}
    else:
        results = {source_key: {records_field: payload}}
    return State(messages=[], inputs={}, results=results)


def _mock_bulk_write_result() -> Mock:
    result = Mock(spec=BulkWriteResult)
    result.inserted_count = 0
    result.matched_count = 1
    result.modified_count = 1
    result.deleted_count = 0
    result.upserted_count = 1
    result.upserted_ids = {1: "507f1f77bcf86cd799439011"}
    result.acknowledged = True
    return result


def _build_node(**overrides: Any) -> MongoDBUpsertManyNode:
    defaults: dict[str, Any] = {
        "name": "upsert_many",
        "database": "test_db",
        "collection": "rss_feeds",
        "source_result_key": "fetch_rss",
        "filter_fields": ["link"],
        "exclude_fields": ["read"],
        "set_on_insert": {"read": False},
    }
    defaults.update(overrides)
    return MongoDBUpsertManyNode(**defaults)


def test_upsert_many_resolve_records_returns_empty_for_non_mapping_results() -> None:
    node = _build_node()
    state = State(messages=[], inputs={}, results="invalid")

    assert node._resolve_records(state) == []


def test_upsert_many_resolve_records_returns_empty_for_non_list_documents() -> None:
    node = _build_node()
    state = State(
        messages=[],
        inputs={},
        results={"fetch_rss": {"documents": "invalid"}},
    )

    assert node._resolve_records(state) == []


def test_upsert_many_resolve_records_returns_empty_for_non_mapping_record() -> None:
    node = _build_node()
    state = State(
        messages=[],
        inputs={},
        results={
            "fetch_rss": {"documents": [{"link": "https://example.com/1"}, "bad"]}
        },
    )

    assert node._resolve_records(state) == []


@pytest.mark.asyncio
async def test_upsert_many_builds_bulk_operations(
    mongo_context: MongoTestContext,
) -> None:
    mongo_context.collection.bulk_write.return_value = _mock_bulk_write_result()
    node = _build_node()

    result = await node.run(_state_with_records(), RunnableConfig())

    mongo_context.collection.bulk_write.assert_called_once()
    operations = mongo_context.collection.bulk_write.call_args[0][0]
    assert len(operations) == 2
    assert operations[0]._filter == {"link": "https://example.com/1"}
    assert operations[0]._doc == {
        "$set": {
            "link": "https://example.com/1",
            "title": "One",
            "fetched_at": "2026-02-28T09:00:00+00:00",
        },
        "$setOnInsert": {"read": False},
    }
    assert operations[0]._upsert is True

    assert result["data"]["operation"] == "bulk_write"
    assert result["data"]["matched_count"] == 1
    assert result["data"]["modified_count"] == 1
    assert result["data"]["upserted_count"] == 1
    assert result["data"]["upserted_ids"] == {"1": "507f1f77bcf86cd799439011"}


@pytest.mark.asyncio
async def test_upsert_many_honors_set_fields(
    mongo_context: MongoTestContext,
) -> None:
    mongo_context.collection.bulk_write.return_value = _mock_bulk_write_result()
    node = _build_node(set_fields=["title"])

    await node.run(_state_with_records(), RunnableConfig())

    operations = mongo_context.collection.bulk_write.call_args[0][0]
    assert operations[0]._doc == {
        "$set": {"title": "One"},
        "$setOnInsert": {"read": False},
    }


@pytest.mark.asyncio
async def test_upsert_many_accepts_list_source(
    mongo_context: MongoTestContext,
) -> None:
    mongo_context.collection.bulk_write.return_value = _mock_bulk_write_result()
    node = _build_node()

    await node.run(_state_with_records(source_as_list=True), RunnableConfig())

    operations = mongo_context.collection.bulk_write.call_args[0][0]
    assert len(operations) == 2


@pytest.mark.asyncio
async def test_upsert_many_requires_records(
    mongo_context: MongoTestContext,
) -> None:
    node = _build_node(source_result_key="missing")

    with pytest.raises(ValueError, match="No records available to upsert"):
        await node.run(_base_state(), RunnableConfig())


@pytest.mark.asyncio
async def test_upsert_many_requires_filter_fields(
    mongo_context: MongoTestContext,
) -> None:
    node = _build_node(filter_fields=[])

    with pytest.raises(
        ValueError,
        match="filter_fields must contain at least one field",
    ):
        await node.run(_state_with_records(), RunnableConfig())


@pytest.mark.asyncio
async def test_upsert_many_rejects_missing_filter_field(
    mongo_context: MongoTestContext,
) -> None:
    node = _build_node()
    state = _state_with_records(records=[{"title": "Missing link"}])

    with pytest.raises(ValueError, match="missing filter field 'link'"):
        await node.run(state, RunnableConfig())


@pytest.mark.asyncio
async def test_upsert_many_rejects_empty_generated_update(
    mongo_context: MongoTestContext,
) -> None:
    node = _build_node(set_fields=["missing_field"], set_on_insert={})

    with pytest.raises(ValueError, match="Generated update document must not be empty"):
        await node.run(_state_with_records(), RunnableConfig())
