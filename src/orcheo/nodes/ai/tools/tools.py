"""Tools for AI agents."""

from typing import Any
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from orcheo.graph.state import State
from orcheo.nodes.ai.tools.context import get_active_tool_config
from orcheo.nodes.ai.tools.registry import ToolMetadata, tool_registry
from orcheo.nodes.storage.mongodb import (
    MongoDBFindNode,
    MongoDBNode,
)


@tool_registry.register(
    ToolMetadata(
        name="greet_user",
        description="Print a greeting to the user.",
        category="general",
    )
)
@tool
def greet_user(username: str) -> str:
    """Print a greeting to the user."""
    return f"Hello, {username}!"


async def _run_mongodb_node(
    node: MongoDBNode, config: RunnableConfig | None = None
) -> dict[str, Any]:
    if config is None:  # pragma: no branch
        config = get_active_tool_config()
    state: State = {
        "inputs": {},
        "results": {},
        "structured_response": None,
        "config": None,
        "messages": [],
    }
    runnable_config = config or RunnableConfig()
    resolved = node.resolved_for_run(state, config=runnable_config)
    return await resolved.run(state, runnable_config)


def _normalize_mongodb_sort(
    sort: dict[str, int] | list[dict[str, int]] | None,
) -> dict[str, int] | list[tuple[str, int]] | None:
    if sort is None:
        return None
    if isinstance(sort, dict):
        return sort
    if not isinstance(sort, list):
        raise ValueError("mongodb_find sort must be a dict or list")
    sort_items: list[tuple[str, int]] = []
    for entry in sort:
        if not isinstance(entry, dict):
            raise ValueError("mongodb_find sort list entries must be dicts")
        for key, value in entry.items():
            sort_items.append((key, value))
    return sort_items


def _resolve_config_token(
    value: str, configurable: dict[str, Any], mapping: dict[str, str]
) -> str | None:
    if value in mapping:
        return configurable.get(mapping[value])  # type: ignore[return-value]
    prefix = "{{config.configurable."
    suffix = "}}"
    if value.startswith(prefix) and value.endswith(suffix):
        key = value[len(prefix) : -len(suffix)].strip()
        if key:
            return configurable.get(key)  # type: ignore[return-value]
    return None


def _resolve_mongodb_target(
    database: str,
    collection: str,
    config: RunnableConfig | None,
) -> tuple[str, str]:
    if config is None:
        return database, collection
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return database, collection

    token_map = {
        "events_database": "events_database",
        "events_collection": "events_collection",
        "rsvps_collection": "rsvps_collection",
    }
    resolved_database = _resolve_config_token(database, configurable, token_map)
    resolved_collection = _resolve_config_token(collection, configurable, token_map)
    return (
        resolved_database or database,
        resolved_collection or collection,
    )


@tool_registry.register(
    ToolMetadata(
        name="mongodb_update_one",
        description="Update a single MongoDB document.",
        category="mongodb",
    )
)
@tool
async def mongodb_update_one(
    database: str,
    collection: str,
    filter: dict[str, Any],
    update: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a single MongoDB document."""
    if not isinstance(database, str) or not database:
        raise ValueError("mongodb_update_one requires a database name")
    if not isinstance(collection, str) or not collection:
        raise ValueError("mongodb_update_one requires a collection name")
    if not isinstance(filter, dict):
        raise ValueError("mongodb_update_one requires a filter document")
    if not isinstance(update, dict):
        raise ValueError("mongodb_update_one requires an update document")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("mongodb_update_one options must be a dict")

    tool_config = get_active_tool_config()
    database, collection = _resolve_mongodb_target(database, collection, tool_config)

    node = MongoDBNode(
        name="mongodb_update_one",
        operation="update_one",
        database=database,
        collection=collection,
        filter=filter,
        update=update,
        options=options,
    )
    return await _run_mongodb_node(node, config=tool_config)


@tool_registry.register(
    ToolMetadata(
        name="mongodb_find",
        description="Find MongoDB documents with optional sort and limit.",
        category="mongodb",
    )
)
@tool
async def mongodb_find(
    database: str,
    collection: str,
    filter: dict[str, Any] | None = None,
    projection: dict[str, Any] | list[str] | None = None,
    sort: dict[str, int] | list[dict[str, int]] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Find MongoDB documents."""
    if not isinstance(database, str) or not database:
        raise ValueError("mongodb_find requires a database name")
    if not isinstance(collection, str) or not collection:
        raise ValueError("mongodb_find requires a collection name")
    if filter is None:
        filter = {}
    if not isinstance(filter, dict):
        raise ValueError("mongodb_find requires a filter document")
    if projection is not None and not isinstance(projection, (dict, list)):
        raise ValueError("mongodb_find projection must be a dict or list")
    if isinstance(projection, list) and not all(
        isinstance(field, str) for field in projection
    ):
        raise ValueError("mongodb_find projection list entries must be strings")
    if limit is not None and not isinstance(limit, int):
        raise ValueError("mongodb_find limit must be an int")

    tool_config = get_active_tool_config()
    database, collection = _resolve_mongodb_target(database, collection, tool_config)

    node = MongoDBFindNode(
        name="mongodb_find",
        database=database,
        collection=collection,
        filter=filter,
        projection=projection,
        sort=_normalize_mongodb_sort(sort),
        limit=limit,
    )
    return await _run_mongodb_node(node, config=tool_config)
