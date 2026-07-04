"""Helper utilities for constructing and routing edges."""

from __future__ import annotations
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.types import Send
from orcheo.edges.registry import edge_registry
from orcheo.graph.normalization import normalise_vertex
from orcheo.graph.state import State


def build_edges(edges: Iterable[Any]) -> dict[str, Any]:
    """Instantiate configured edges."""
    edge_instances: dict[str, Any] = {}
    for edge in edges:
        edge_type = edge.get("type")
        edge_name = edge.get("name")
        if not edge_name:
            msg = "Edge must have a name"
            raise ValueError(msg)
        edge_class = edge_registry.get_edge(str(edge_type))
        if edge_class is None:
            msg = f"Unknown edge type: {edge_type}"
            raise ValueError(msg)
        edge_params = {k: v for k, v in edge.items() if k != "type"}
        edge_instances[str(edge_name)] = edge_class(**edge_params)
    return edge_instances


def build_edge_router(
    edge: Callable[[State, RunnableConfig], Awaitable[Any]],
    mapping: Mapping[str, Any],
    default_target: Any | None,
) -> Callable[[State, RunnableConfig], Awaitable[Any]]:
    """Return an async router that normalises edge outputs."""
    normalised_mapping_for_edge: dict[str, Any] = {
        str(key): normalise_vertex(str(target)) for key, target in mapping.items()
    }
    resolved_default = None
    if isinstance(default_target, str) and default_target:
        resolved_default = normalise_vertex(default_target)

    async def _route_edge(state: State, config: RunnableConfig) -> Any:
        result = await edge(state, config)
        if isinstance(result, Sequence) and not isinstance(result, str | bytes):
            return [
                _coerce_edge_destination(
                    item, normalised_mapping_for_edge, resolved_default
                )
                for item in result
            ]
        return _coerce_edge_destination(
            result, normalised_mapping_for_edge, resolved_default
        )

    return _route_edge


def _coerce_edge_destination(
    value: Any,
    mapping: Mapping[str, Any],
    default_target: Any | None,
) -> Any:
    """Return a normalised destination for an edge result."""
    if isinstance(value, Send):
        return value
    normalised = mapping.get(str(value))
    if normalised is not None:
        return normalised
    if default_target is not None:
        return default_target
    msg = (
        f"Conditional edge produced value {value!r}, which is not in mapping "
        f"{sorted(mapping)} and no 'default' target was configured. Add a "
        "'default' (use 'END' to end the run intentionally)."
    )
    raise ValueError(msg)
