"""Registry implementation for Orcheo agent tools."""

from collections.abc import Callable
from langchain_core.tools import BaseTool
from pydantic import BaseModel


class ToolMetadata(BaseModel):
    """Metadata for a tool in the agent.

    Attributes:
        name: Unique identifier for the tool
        description: Human-readable description of the tool's purpose
        category: Tool category, defaults to "general"
    """

    name: str
    """Unique identifier for the tool."""
    description: str
    """Human-readable description of the tool's purpose."""
    category: str = "general"
    """Tool category, defaults to "general"."""


class ToolRegistry:
    """Registry for managing agent tools and their metadata."""

    def __init__(self) -> None:
        """Initialize an empty tool registry."""
        self._tools: dict[str, BaseTool | Callable] = {}
        self._metadata: dict[str, ToolMetadata] = {}

    def register(
        self, metadata: ToolMetadata
    ) -> Callable[[Callable | BaseTool], Callable | BaseTool]:
        """Register a new tool with its metadata.

        Args:
            metadata: Tool metadata including name and description

        Returns:
            Decorator function that registers the tool or tool factory
        """

        def decorator(func: Callable | BaseTool) -> Callable | BaseTool:
            self._tools[metadata.name] = func
            self._metadata[metadata.name] = metadata
            return func

        return decorator

    def get_tool(self, name: str) -> BaseTool | Callable | None:
        """Get a tool by name.

        Args:
            name: Name of the tool to retrieve

        Returns:
            Tool instance or callable, or None if not found
        """
        return self._tools.get(name)

    def get_metadata(self, name: str) -> ToolMetadata | None:
        """Return metadata for the tool identified by ``name`` if available."""
        return self._metadata.get(name)

    def list_metadata(self) -> list[ToolMetadata]:
        """Return all registered tool metadata entries sorted by name."""
        return sorted(self._metadata.values(), key=lambda item: item.name.lower())

    def get_metadata_by_callable(self, obj: Callable) -> ToolMetadata | None:
        """Return metadata associated with a registered callable."""
        for name, registered in self._tools.items():
            if registered is obj:
                return self._metadata.get(name)
            if isinstance(registered, type) and isinstance(obj, registered):
                return self._metadata.get(name)
        return None

    def unregister(self, name: str) -> None:
        """Remove one registered tool and its metadata if present."""
        self._tools.pop(name, None)
        self._metadata.pop(name, None)


# Global registry instance
tool_registry = ToolRegistry()
