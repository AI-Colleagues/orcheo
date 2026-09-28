"""MCP tools describing the nodes, edges and agent tools this server provides."""

from __future__ import annotations
from collections.abc import Callable
from importlib import import_module
from typing import Annotated, Any, Literal, Protocol
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from orcheo.plugins import ensure_plugins_loaded
from orcheo_backend.app.mcp_server._shared import READ_ONLY


CatalogKind = Literal["node", "edge", "agent_tool"]
KindArg = Annotated[
    CatalogKind,
    Field(
        description=(
            "'node' for workflow nodes, 'edge' for conditional edges, "
            "'agent_tool' for tools that agent nodes can call."
        )
    ),
]


class _Metadata(Protocol):
    name: str
    category: str
    description: str


class _Registry(Protocol):
    def list_metadata(self) -> list[Any]: ...  # pragma: no cover

    def get_metadata(self, name: str) -> Any: ...  # pragma: no cover


def _registry(kind: CatalogKind) -> tuple[_Registry, Callable[[str], Any]]:
    """Return the registry for ``kind`` and its component lookup function."""
    ensure_plugins_loaded()
    if kind == "node":
        from orcheo.nodes.registry import registry

        return registry, registry.get_node
    if kind == "edge":
        from orcheo.edges.registry import edge_registry

        return edge_registry, edge_registry.get_edge
    # Importing the tools module registers the built-in agent tools.
    import_module("orcheo.nodes.ai.tools.tools")
    from orcheo.nodes.ai.tools.registry import tool_registry

    return tool_registry, tool_registry.get_tool


def _summary(metadata: _Metadata) -> dict[str, str]:
    return {
        "name": metadata.name,
        "category": metadata.category,
        "description": metadata.description,
    }


def _component_schema(component: Any) -> dict[str, Any] | None:
    """Return the JSON schema describing a component's configuration."""
    args_schema = getattr(component, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        return args_schema.model_json_schema()
    if hasattr(component, "model_json_schema"):
        return component.model_json_schema()
    return None


def register_catalog_tools(server: FastMCP) -> None:
    """Register node, edge and agent-tool discovery tools on ``server``."""

    @server.tool(annotations=READ_ONLY)
    def list_components(
        kind: KindArg,
        query: Annotated[
            str | None,
            Field(description="Case-insensitive filter on name or category."),
        ] = None,
    ) -> dict[str, Any]:
        """List the registered nodes, edges or agent tools, including plugins."""
        registry, _ = _registry(kind)
        entries = [_summary(item) for item in registry.list_metadata()]
        if query:
            needle = query.lower()
            entries = [
                entry
                for entry in entries
                if needle in entry["name"].lower()
                or needle in entry["category"].lower()
            ]
        return {"kind": kind, "components": entries}

    @server.tool(annotations=READ_ONLY)
    def describe_component(
        kind: KindArg,
        name: Annotated[str, Field(description="Registered component name.")],
    ) -> dict[str, Any]:
        """Return a node, edge or agent tool's description and config schema."""
        registry, lookup = _registry(kind)
        metadata = registry.get_metadata(name)
        component = lookup(name)
        if metadata is None or component is None:
            raise ToolError(
                f"No {kind.replace('_', ' ')} named '{name}' is registered."
            )
        result: dict[str, Any] = _summary(metadata)
        schema = _component_schema(component)
        if schema is not None:
            result["schema"] = schema
        return result


__all__ = ["register_catalog_tools"]
