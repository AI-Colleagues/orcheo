"""Tests for the agent tool registry module."""

from __future__ import annotations
from orcheo.nodes.ai.tools.registry import ToolMetadata, ToolRegistry


def _make_registry() -> ToolRegistry:
    return ToolRegistry()


def test_register_and_get_tool() -> None:
    """register stores tool; get_tool retrieves it."""
    registry = _make_registry()
    meta = ToolMetadata(name="search", description="Search tool")

    def tool(query: str) -> str:
        return f"results for {query}"

    registry.register(meta)(tool)

    assert registry.get_tool("search") is tool


def test_get_tool_unknown_returns_none() -> None:
    """get_tool returns None for unknown names."""
    registry = _make_registry()
    assert registry.get_tool("unknown") is None


def test_get_metadata() -> None:
    """get_metadata returns metadata for registered tool."""
    registry = _make_registry()
    meta = ToolMetadata(name="calc", description="Calculator", category="math")

    def identity(x: object) -> object:
        return x

    registry.register(meta)(identity)

    assert registry.get_metadata("calc") is meta
    assert registry.get_metadata("unknown") is None


def test_list_metadata_sorted() -> None:
    """list_metadata returns entries sorted by name case-insensitively."""
    registry = _make_registry()

    def noop() -> None:
        pass

    registry.register(ToolMetadata(name="Zap", description=""))(noop)
    registry.register(ToolMetadata(name="alpha", description=""))(noop)
    registry.register(ToolMetadata(name="Beta", description=""))(noop)

    names = [m.name for m in registry.list_metadata()]
    assert names == ["alpha", "Beta", "Zap"]


def test_get_metadata_by_callable_exact_match() -> None:
    """get_metadata_by_callable returns metadata for exact callable match."""
    registry = _make_registry()

    def tool(x: object) -> object:
        return x

    meta = ToolMetadata(name="exact", description="")
    registry.register(meta)(tool)

    result = registry.get_metadata_by_callable(tool)
    assert result is meta


def test_get_metadata_by_callable_instance_match() -> None:
    """get_metadata_by_callable returns metadata for class instance match."""
    registry = _make_registry()

    class MyTool:
        def __call__(self) -> str:
            return "result"

    meta = ToolMetadata(name="my_tool", description="")
    registry.register(meta)(MyTool)

    instance = MyTool()
    result = registry.get_metadata_by_callable(instance)
    assert result is meta


def test_get_metadata_by_callable_no_match() -> None:
    """get_metadata_by_callable returns None for unknown callable."""
    registry = _make_registry()

    def unknown() -> None:
        pass

    assert registry.get_metadata_by_callable(unknown) is None


def test_unregister_removes_tool_and_metadata() -> None:
    """unregister removes both tool and metadata (lines 82-83)."""
    registry = _make_registry()
    meta = ToolMetadata(name="temp", description="Temporary tool")

    def temp_tool() -> str:
        return "temp"

    registry.register(meta)(temp_tool)

    registry.unregister("temp")

    assert registry.get_tool("temp") is None
    assert registry.get_metadata("temp") is None


def test_unregister_unknown_is_noop() -> None:
    """unregister silently ignores unknown names."""
    registry = _make_registry()
    registry.unregister("not-there")  # should not raise


def test_get_metadata_by_callable_skips_non_type_entries() -> None:
    """get_metadata_by_callable loops past non-matching function entries (76->73)."""
    registry = _make_registry()

    def tool_a() -> str:
        return "a"

    def tool_b() -> str:
        return "b"

    meta = ToolMetadata(name="tool_a", description="")
    registry.register(meta)(tool_a)

    # tool_b is not registered; loop iterates over tool_a (not a type, not obj) → 76->73
    result = registry.get_metadata_by_callable(tool_b)
    assert result is None
