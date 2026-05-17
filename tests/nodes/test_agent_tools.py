"""Tests for agent tools registry and tools."""

from langchain_core.tools import BaseTool
from orcheo.nodes.ai.tools.registry import ToolMetadata, ToolRegistry
from orcheo.nodes.ai.tools.tools import greet_user


def test_tool_registry_get_tool_returns_none():
    """Test get_tool returns None for unregistered tool."""
    registry = ToolRegistry()
    assert registry.get_tool("nonexistent") is None


def test_tool_registry_get_metadata_returns_none():
    """Test get_metadata returns None for unregistered tool."""
    registry = ToolRegistry()
    assert registry.get_metadata("nonexistent") is None


def test_tool_registry_get_metadata_returns_metadata():
    """Test get_metadata returns metadata for registered tool."""
    registry = ToolRegistry()

    def my_tool():
        return "result"

    metadata = ToolMetadata(name="my_tool", description="A test tool", category="test")
    registry.register(metadata)(my_tool)

    result = registry.get_metadata("my_tool")
    assert result is not None
    assert result.name == "my_tool"
    assert result.description == "A test tool"
    assert result.category == "test"


def test_tool_registry_list_metadata():
    """Test list_metadata returns sorted metadata."""
    registry = ToolRegistry()

    def tool_b():
        return "b"

    def tool_a():
        return "a"

    metadata_b = ToolMetadata(name="tool_B", description="Tool B", category="test")
    metadata_a = ToolMetadata(name="tool_a", description="Tool A", category="test")

    registry.register(metadata_b)(tool_b)
    registry.register(metadata_a)(tool_a)

    result = registry.list_metadata()
    assert len(result) == 2
    assert result[0].name == "tool_a"
    assert result[1].name == "tool_B"


def test_tool_registry_get_metadata_by_callable_exact_match():
    """Test get_metadata_by_callable returns metadata for exact callable match."""
    registry = ToolRegistry()

    def my_tool():
        return "result"

    metadata = ToolMetadata(name="my_tool", description="A test tool", category="test")
    registry.register(metadata)(my_tool)

    result = registry.get_metadata_by_callable(my_tool)
    assert result is not None
    assert result.name == "my_tool"


def test_tool_registry_get_metadata_by_callable_instance_match():
    """Test get_metadata_by_callable returns metadata"""
    registry = ToolRegistry()

    class MyTool(BaseTool):
        name: str = "my_tool"
        description: str = "A test tool"

        def _run(self, *args, **kwargs):
            return "result"

    metadata = ToolMetadata(
        name="my_tool_class", description="A class-based tool", category="test"
    )
    registry.register(metadata)(MyTool)

    instance = MyTool()
    result = registry.get_metadata_by_callable(instance)
    assert result is not None
    assert result.name == "my_tool_class"


def test_tool_registry_get_metadata_by_callable_no_match():
    """Test get_metadata_by_callable returns None for unregistered callable."""
    registry = ToolRegistry()

    def unregistered_tool():
        return "result"

    result = registry.get_metadata_by_callable(unregistered_tool)
    assert result is None


def test_greet_user_tool():
    """Test greet_user tool execution."""
    result = greet_user.invoke({"username": "Alice"})
    assert result == "Hello, Alice!"


def test_greet_user_tool_different_username():
    """Test greet_user tool with different username."""
    result = greet_user.invoke({"username": "Bob"})
    assert result == "Hello, Bob!"


def test_tool_registry_get_metadata_by_callable_multiple_tools():
    """Test get_metadata_by_callable with multiple registered tools."""
    registry = ToolRegistry()

    def tool_a():
        return "a"

    def tool_b():
        return "b"

    class ToolC(BaseTool):
        name: str = "tool_c"
        description: str = "Tool C"

        def _run(self, *args, **kwargs):
            return "c"

    metadata_a = ToolMetadata(name="tool_a", description="Tool A", category="test")
    metadata_b = ToolMetadata(name="tool_b", description="Tool B", category="test")
    metadata_c = ToolMetadata(name="tool_c", description="Tool C", category="test")

    registry.register(metadata_a)(tool_a)
    registry.register(metadata_b)(tool_b)
    registry.register(metadata_c)(ToolC)

    # Test finding tool_b (iterates past tool_a)
    result = registry.get_metadata_by_callable(tool_b)
    assert result is not None
    assert result.name == "tool_b"

    # Test finding instance of ToolC (iterates past tool_a and tool_b)
    instance = ToolC()
    result = registry.get_metadata_by_callable(instance)
    assert result is not None
    assert result.name == "tool_c"
