"""Registry implementation for Orcheo edges."""

from collections.abc import Callable
from pydantic import BaseModel


class EdgeMetadata(BaseModel):
    """Metadata for an edge in the flow.

    Attributes:
        name: Unique identifier for the edge
        description: Human-readable description of the edge's purpose
        category: Edge category, defaults to "logic"
    """

    name: str
    """Unique identifier for the edge."""
    description: str
    """Human-readable description of the edge's purpose."""
    category: str = "logic"
    """Edge category, defaults to "logic"."""


class EdgeRegistry:
    """Registry for managing flow edges and their metadata."""

    def __init__(self) -> None:
        """Initialize an empty edge registry."""
        self._edges: dict[str, Callable] = {}
        self._metadata: dict[str, EdgeMetadata] = {}

    def register(self, metadata: EdgeMetadata) -> Callable[[Callable], Callable]:
        """Register a new edge with its metadata.

        Args:
            metadata: Edge metadata including name and schemas

        Returns:
            Decorator function that registers the edge implementation
        """

        def decorator(func: Callable) -> Callable:
            if metadata.name in self._edges:
                msg = f"Edge '{metadata.name}' is already registered."
                raise ValueError(msg)
            self._edges[metadata.name] = func
            self._metadata[metadata.name] = metadata
            return func

        return decorator

    def get_edge(self, name: str) -> Callable | None:
        """Get an edge implementation by name.

        Args:
            name: Name of the edge to retrieve

        Returns:
            Edge implementation function or None if not found
        """
        return self._edges.get(name)

    def get_metadata(self, name: str) -> EdgeMetadata | None:
        """Return metadata for the edge identified by ``name`` if available."""
        return self._metadata.get(name)

    def list_metadata(self) -> list[EdgeMetadata]:
        """Return all registered edge metadata entries sorted by name."""
        return sorted(self._metadata.values(), key=lambda item: item.name.lower())

    def get_metadata_by_callable(self, obj: Callable) -> EdgeMetadata | None:
        """Return metadata associated with a registered callable."""
        for name, registered in self._edges.items():
            if registered is obj:
                return self._metadata.get(name)
            if isinstance(registered, type) and isinstance(obj, registered):
                return self._metadata.get(name)
        return None

    def unregister(self, name: str) -> None:
        """Remove one registered edge."""
        self._edges.pop(name, None)
        self._metadata.pop(name, None)


# Global registry instance
edge_registry = EdgeRegistry()


__all__ = ["EdgeMetadata", "EdgeRegistry", "edge_registry"]
