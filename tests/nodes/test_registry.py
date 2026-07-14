"""Tests for the node registry module."""

from orcheo.nodes.registry import NodeMetadata, NodeRegistry


def test_get_metadata_by_callable_exact_match() -> None:
    """get_metadata_by_callable returns metadata for exact callable match."""

    registry = NodeRegistry()

    def my_node(state):
        return state

    metadata = NodeMetadata(
        name="my_node",
        description="A test node",
        category="test",
    )

    registry.register(metadata)(my_node)

    result = registry.get_metadata_by_callable(my_node)
    assert result is not None
    assert result.name == "my_node"
    assert result.description == "A test node"
    assert result.category == "test"
    assert result.restricted is False


def test_metadata_can_mark_node_restricted() -> None:
    """Node metadata exposes an opt-in restricted-mode policy flag."""
    metadata = NodeMetadata(
        name="restricted_node",
        description="Host-capable node",
        restricted=True,
    )

    assert metadata.restricted is True


def test_get_metadata_by_callable_instance_match() -> None:
    """get_metadata_by_callable returns metadata for instance of registered class."""

    registry = NodeRegistry()

    class MyNode:
        def __call__(self, state):
            return state

    metadata = NodeMetadata(
        name="class_node",
        description="A class-based node",
        category="classes",
    )

    registry.register(metadata)(MyNode)

    instance = MyNode()
    result = registry.get_metadata_by_callable(instance)
    assert result is not None
    assert result.name == "class_node"
    assert result.description == "A class-based node"


def test_get_metadata_by_callable_no_match() -> None:
    """get_metadata_by_callable returns None for unregistered callable."""

    registry = NodeRegistry()

    def unregistered_node(state):
        return state

    result = registry.get_metadata_by_callable(unregistered_node)
    assert result is None


def test_get_metadata_returns_registered_entry() -> None:
    """get_metadata surfaces registered metadata by node name."""

    registry = NodeRegistry()
    metadata = NodeMetadata(name="alpha", description="Alpha node", category="demo")
    registry.register(metadata)(lambda _: None)

    assert registry.get_metadata("alpha") is metadata
    assert registry.get_metadata("missing") is None


def test_list_metadata_returns_sorted_entries() -> None:
    """list_metadata returns metadata sorted by case-insensitive name."""

    registry = NodeRegistry()
    first = NodeMetadata(name="Beta", description="", category="")
    second = NodeMetadata(name="alpha", description="", category="")
    registry.register(first)(lambda _: None)
    registry.register(second)(lambda _: None)

    names = [item.name for item in registry.list_metadata()]
    assert names == ["alpha", "Beta"]
