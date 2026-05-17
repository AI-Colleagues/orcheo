"""Agent tool service operations.

Pure business logic for agent tool operations, shared by CLI and MCP interfaces.
"""

from __future__ import annotations
from importlib import import_module
from typing import TYPE_CHECKING, Any
from orcheo_sdk.cli.errors import CLIError


if TYPE_CHECKING:
    from orcheo.nodes.ai.tools.registry import ToolRegistry


def load_tool_registry() -> ToolRegistry:
    """Load the global tool registry from orcheo.nodes.ai.tools.registry.

    Returns:
        ToolRegistry instance

    Raises:
        CLIError: If unable to import or load the registry
    """
    from orcheo.nodes.ai.tools.registry import ToolRegistry
    from orcheo.plugins import ensure_plugins_loaded

    ensure_plugins_loaded()
    try:
        # Import tools module to trigger registration
        import_module("orcheo.nodes.ai.tools.tools")
    except ModuleNotFoundError as exc:  # pragma: no cover - import error
        msg = "Unable to import orcheo.nodes.ai.tools.tools for registry population"
        raise CLIError(msg) from exc

    try:
        module = import_module("orcheo.nodes.ai.tools.registry")
    except ModuleNotFoundError as exc:  # pragma: no cover - import error
        msg = "Unable to import orcheo.nodes.ai.tools.registry"
        raise CLIError(msg) from exc

    registry = getattr(module, "tool_registry", None)
    if registry is None:  # pragma: no cover - defensive
        msg = (
            "orcheo.nodes.ai.tools.registry does not expose a 'tool_registry' attribute"
        )
        raise CLIError(msg)

    if not isinstance(registry, ToolRegistry):  # pragma: no cover - defensive
        msg = "Loaded registry is not an instance of ToolRegistry"
        raise CLIError(msg)
    return registry


def list_agent_tools_data(category: str | None = None) -> list[dict[str, Any]]:
    """Get list of registered agent tools.

    Args:
        category: Optional category filter

    Returns:
        List of agent tool metadata objects
    """
    registry = load_tool_registry()
    entries = registry.list_metadata()

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


def show_agent_tool_data(name: str) -> dict[str, Any]:
    """Get agent tool metadata and schema.

    Args:
        name: Tool name

    Returns:
        Dictionary with tool metadata and schema

    Raises:
        CLIError: If tool is not registered
    """
    registry = load_tool_registry()
    metadata = registry.get_metadata(name)
    tool = registry.get_tool(name)

    if metadata is None or tool is None:
        raise CLIError(f"Agent tool '{name}' is not registered.")

    result: dict[str, Any] = {
        "name": metadata.name,
        "category": metadata.category,
        "description": metadata.description,
    }

    # Try to extract schema from the tool
    schema_data: dict[str, Any] = {}
    if hasattr(tool, "args_schema") and tool.args_schema is not None:
        # LangChain tool with Pydantic schema
        if hasattr(tool.args_schema, "model_json_schema"):
            schema_data = tool.args_schema.model_json_schema()
    elif hasattr(tool, "model_json_schema"):
        # Direct Pydantic model
        schema_data = tool.model_json_schema()
    elif hasattr(tool, "__annotations__"):
        # Function with type annotations
        annotations = getattr(tool, "__annotations__", {})
        if annotations:
            schema_data = {
                "type": "object",
                "properties": {
                    key: {"type": str(val)} for key, val in annotations.items()
                },
            }
    else:  # pragma: no cover - fallback for unexpected tool implementations
        pass

    if schema_data:
        result["schema"] = schema_data

    return result
