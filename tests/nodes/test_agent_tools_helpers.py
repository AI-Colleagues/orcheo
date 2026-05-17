"""Helper tests for MongoDB agent tools."""

from __future__ import annotations
from typing import Any
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.nodes.ai.tools import tools


@pytest.mark.asyncio
async def test_run_mongodb_node_uses_active_config(monkeypatch) -> None:
    captured: dict[str, RunnableConfig | None] = {}

    class FakeNode:
        def resolved_for_run(self, state, config=None):  # type: ignore[no-untyped-def]
            captured["resolved_config"] = config
            return self

        async def run(self, state, config):  # type: ignore[no-untyped-def]
            captured["run_config"] = config
            return {"ok": True}

    config = RunnableConfig(configurable={"value": "A"})
    called: dict[str, bool] = {}

    def fake_get_config() -> RunnableConfig:
        called["requested"] = True
        return config

    monkeypatch.setattr(tools, "get_active_tool_config", fake_get_config)

    result = await tools._run_mongodb_node(FakeNode())
    assert result == {"ok": True}
    assert captured["resolved_config"] is config
    assert captured["run_config"] is config
    assert called.get("requested")


def test_normalize_mongodb_sort_with_list() -> None:
    tuples = tools._normalize_mongodb_sort(
        [{"first": 1}, {"second": -1}],
    )
    assert tuples == [("first", 1), ("second", -1)]


def test_normalize_mongodb_sort_requires_dict_or_list() -> None:
    with pytest.raises(ValueError, match="dict or list"):
        tools._normalize_mongodb_sort("invalid")  # type: ignore[arg-type]


def test_normalize_mongodb_sort_list_entries_must_be_dicts() -> None:
    with pytest.raises(ValueError, match="must be dicts"):
        tools._normalize_mongodb_sort([{"a": 1}, "invalid"])  # type: ignore[arg-type]


def test_resolve_config_token_handles_mapping_and_templates() -> None:
    configurable = {"mapped": "value", "templated": "value"}
    mapping = {"mapped": "mapped"}

    assert tools._resolve_config_token("mapped", configurable, mapping) == "value"
    assert (
        tools._resolve_config_token(
            "{{config.configurable.templated}}",
            configurable,
            mapping,
        )
        == "value"
    )
    assert tools._resolve_config_token("missing", configurable, mapping) is None


def test_resolve_config_token_handles_empty_templates() -> None:
    configurable = {"key": "value"}
    mapping: dict[str, str] = {}

    assert (
        tools._resolve_config_token(
            "{{config.configurable.}}",
            configurable,
            mapping,
        )
        is None
    )


def test_resolve_mongodb_target_returns_original_without_config() -> None:
    assert tools._resolve_mongodb_target("db", "col", None) == ("db", "col")


def test_resolve_mongodb_target_ignores_non_dict_config() -> None:
    config = RunnableConfig(configurable="invalid")  # type: ignore[arg-type]
    assert tools._resolve_mongodb_target("db", "col", config) == ("db", "col")


def test_resolve_mongodb_target_resolves_tokens() -> None:
    config = RunnableConfig(
        configurable={
            "events_database": "AIC",
            "events_collection": "events",
        }
    )

    assert tools._resolve_mongodb_target(
        "events_database",
        "events_collection",
        config,
    ) == ("AIC", "events")


@pytest.mark.asyncio
async def test_mongodb_update_one_defaults_options(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    async def fake_run(node, config=None):
        captured["options"] = node.options
        return {"ok": True}

    monkeypatch.setattr(tools, "_run_mongodb_node", fake_run)

    await tools.mongodb_update_one.coroutine(
        database="db",
        collection="col",
        filter={},
        update={},
    )

    assert captured["options"] == {}


@pytest.mark.asyncio
async def test_mongodb_update_one_validates_required_fields() -> None:
    payload = {
        "database": "",
        "collection": "c",
        "filter": {},
        "update": {},
    }
    with pytest.raises(ValueError, match="requires a database name"):
        await tools.mongodb_update_one.coroutine(**payload)

    payload["database"] = "db"
    payload["collection"] = ""
    with pytest.raises(ValueError, match="requires a collection name"):
        await tools.mongodb_update_one.coroutine(**payload)

    payload["collection"] = "c"
    payload["filter"] = "bad"
    with pytest.raises(ValueError, match="filter document"):
        await tools.mongodb_update_one.coroutine(**payload)

    payload["filter"] = {}
    payload["update"] = "bad"
    with pytest.raises(ValueError, match="update document"):
        await tools.mongodb_update_one.coroutine(**payload)

    payload["update"] = {}
    with pytest.raises(ValueError, match="options must be a dict"):
        await tools.mongodb_update_one.coroutine(
            **{**payload, "options": "invalid"},  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_mongodb_find_validates_arguments() -> None:
    payload = {
        "database": "",
        "collection": "col",
        "filter": {},
    }
    with pytest.raises(ValueError, match="requires a database name"):
        await tools.mongodb_find.coroutine(**payload)

    payload["database"] = "db"
    payload["collection"] = ""
    with pytest.raises(ValueError, match="requires a collection name"):
        await tools.mongodb_find.coroutine(**payload)

    payload["collection"] = "col"
    payload["filter"] = "bad"
    with pytest.raises(ValueError, match="filter document"):
        await tools.mongodb_find.coroutine(**payload)

    payload["filter"] = {}
    payload["limit"] = "five"
    with pytest.raises(ValueError, match="limit must be an int"):
        await tools.mongodb_find.coroutine(**payload)


@pytest.mark.asyncio
async def test_mongodb_find_projection_requires_dict_or_list() -> None:
    payload = {
        "database": "db",
        "collection": "col",
        "filter": {},
        "projection": 123,
    }
    with pytest.raises(ValueError, match="projection must be a dict or list"):
        await tools.mongodb_find.coroutine(**payload)


@pytest.mark.asyncio
async def test_mongodb_find_projection_list_entries_strings() -> None:
    payload = {
        "database": "db",
        "collection": "col",
        "filter": {},
        "projection": ["valid", 123],
    }
    with pytest.raises(ValueError, match="projection list entries must be strings"):
        await tools.mongodb_find.coroutine(**payload)
