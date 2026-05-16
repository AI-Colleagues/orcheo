"""Tests for MongoDB agent tools."""

from __future__ import annotations
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.nodes.agent_tools import tools
from orcheo.nodes.agent_tools.context import tool_execution_context
from orcheo.nodes.integrations.databases.mongodb import (
    MongoDBFindNode,
    MongoDBNode,
)


@pytest.mark.asyncio
async def test_mongodb_update_one_tool_builds_node(monkeypatch):
    """mongodb_update_one should build a MongoDBNode with provided inputs."""
    captured: dict[str, MongoDBNode] = {}

    async def fake_run(node, config=None):
        captured["node"] = node
        return {"ok": True}

    monkeypatch.setattr(tools, "_run_mongodb_node", fake_run)

    result = await tools.mongodb_update_one.ainvoke(
        {
            "database": "db",
            "collection": "events",
            "filter": {"event_id": "1"},
            "update": {"$set": {"title": "Test"}},
            "options": {"upsert": True},
        }
    )

    assert result == {"ok": True}
    node = captured["node"]
    assert isinstance(node, MongoDBNode)
    assert node.operation == "update_one"
    assert node.database == "db"
    assert node.collection == "events"
    assert node.filter == {"event_id": "1"}
    assert node.update == {"$set": {"title": "Test"}}
    assert node.options == {"upsert": True}


@pytest.mark.asyncio
async def test_mongodb_find_tool_builds_node(monkeypatch):
    """mongodb_find should build a MongoDBFindNode with provided inputs."""
    captured: dict[str, MongoDBFindNode] = {}

    async def fake_run(node, config=None):
        captured["node"] = node
        return {"data": []}

    monkeypatch.setattr(tools, "_run_mongodb_node", fake_run)

    result = await tools.mongodb_find.ainvoke(
        {
            "database": "db",
            "collection": "rsvps",
            "filter": {"event_id": "1"},
            "projection": {"_id": 1, "updated_at": 1},
            "sort": {"updated_at": -1},
            "limit": 5,
        }
    )

    assert result == {"data": []}
    node = captured["node"]
    assert isinstance(node, MongoDBFindNode)
    assert node.operation == "find"
    assert node.database == "db"
    assert node.collection == "rsvps"
    assert node.filter == {"event_id": "1"}
    assert node.projection == {"_id": 1, "updated_at": 1}
    assert node.sort == {"updated_at": -1}
    assert node.limit == 5


@pytest.mark.asyncio
async def test_mongodb_find_tool_default_filter(monkeypatch):
    """mongodb_find should default to an empty filter when omitted."""
    captured: dict[str, MongoDBFindNode] = {}

    async def fake_run(node, config=None):
        captured["node"] = node
        return {"data": []}

    monkeypatch.setattr(tools, "_run_mongodb_node", fake_run)

    await tools.mongodb_find.ainvoke(
        {
            "database": "db",
            "collection": "events",
        }
    )

    node = captured["node"]
    assert node.filter == {}


@pytest.mark.asyncio
async def test_mongodb_tool_resolves_config_tokens(monkeypatch):
    """mongodb_find should resolve config token values."""
    captured: dict[str, MongoDBFindNode] = {}

    async def fake_run(node, config=None):
        captured["node"] = node
        return {"data": []}

    monkeypatch.setattr(tools, "_run_mongodb_node", fake_run)

    config = RunnableConfig(
        configurable={
            "events_database": "AIC",
            "rsvps_collection": "event_rsvps",
        }
    )

    with tool_execution_context(config):
        await tools.mongodb_find.ainvoke(
            {
                "database": "events_database",
                "collection": "rsvps_collection",
            }
        )

    node = captured["node"]
    assert node.database == "AIC"
    assert node.collection == "event_rsvps"
