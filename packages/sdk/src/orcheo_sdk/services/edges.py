"""Edge service operations.

Pure business logic for edge operations, shared by CLI and MCP interfaces.
"""

from __future__ import annotations
from typing import Any
from orcheo_sdk.cli.errors import CLIError


def list_edges_data(category: str | None = None) -> list[dict[str, Any]]:
    """Get list of registered edges.

    Args:
        category: Optional category filter for edges

    Returns:
        List of edge metadata objects
    """
    from orcheo.edges.registry import edge_registry
    from orcheo.plugins import ensure_plugins_loaded

    ensure_plugins_loaded()
    entries = edge_registry.list_metadata()

    if category:
        lowered = category.lower()
        entries = [
            item
            for item in entries
            if lowered in item.category.lower() or lowered in item.name.lower()
        ]

    return [
        {
            "name": item.name,
            "category": item.category,
            "description": item.description,
        }
        for item in entries
    ]


def show_edge_data(name: str) -> dict[str, Any]:
    """Get edge metadata and schema.

    Args:
        name: Edge name

    Returns:
        Dictionary with edge metadata and schema

    Raises:
        CLIError: If edge is not registered
    """
    from orcheo.edges.registry import edge_registry
    from orcheo.plugins import ensure_plugins_loaded

    ensure_plugins_loaded()
    metadata = edge_registry.get_metadata(name)
    edge_cls = edge_registry.get_edge(name)

    if metadata is None or edge_cls is None:
        raise CLIError(f"Edge '{name}' is not registered.")

    result: dict[str, Any] = {
        "name": metadata.name,
        "category": metadata.category,
        "description": metadata.description,
    }

    if hasattr(edge_cls, "model_json_schema"):
        result["schema"] = edge_cls.model_json_schema()
    else:
        annotations = getattr(edge_cls, "__annotations__", {})
        if annotations:
            result["attributes"] = list(annotations.keys())

    return result
