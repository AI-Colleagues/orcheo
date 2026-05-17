"""Tests for the PluginAPI registration surface."""

from __future__ import annotations
from typing import Any
import pytest
from orcheo.edges.registry import EdgeMetadata, EdgeRegistry
from orcheo.listeners.registry import (
    ListenerMetadata,
    ListenerRegistry,
)
from orcheo.nodes.ai.tools.registry import ToolMetadata, ToolRegistry
from orcheo.nodes.registry import NodeMetadata, NodeRegistry
from orcheo.plugins.api import PluginAPI, PluginRegistrations
from orcheo.triggers.registry import TriggerMetadata, TriggerRegistry


# ---------------------------------------------------------------------------
# Isolated registry stubs to avoid polluting global state
# ---------------------------------------------------------------------------


def _make_api_with_fresh_registries() -> tuple[
    PluginAPI,
    NodeRegistry,
    EdgeRegistry,
    ToolRegistry,
    TriggerRegistry,
    ListenerRegistry,
]:
    """Return a PluginAPI wired to fresh, isolated registries."""
    node_reg = NodeRegistry()
    edge_reg = EdgeRegistry()
    tool_reg = ToolRegistry()
    trigger_reg = TriggerRegistry()
    listener_reg = ListenerRegistry()

    api = PluginAPI.__new__(PluginAPI)
    api.registrations = PluginRegistrations()

    # Patch the module-level singletons used inside PluginAPI methods
    import orcheo.plugins.api as api_module

    api_module.registry = node_reg
    api_module.edge_registry = edge_reg
    api_module.tool_registry = tool_reg
    api_module.trigger_registry = trigger_reg
    api_module.listener_registry = listener_reg

    return api, node_reg, edge_reg, tool_reg, trigger_reg, listener_reg


def _restore_registries() -> None:
    import orcheo.plugins.api as api_module
    from orcheo.edges.registry import edge_registry
    from orcheo.listeners.registry import listener_registry
    from orcheo.nodes.ai.tools.registry import tool_registry
    from orcheo.nodes.registry import registry
    from orcheo.triggers.registry import trigger_registry

    api_module.registry = registry
    api_module.edge_registry = edge_registry
    api_module.tool_registry = tool_registry
    api_module.trigger_registry = trigger_registry
    api_module.listener_registry = listener_registry


# ---------------------------------------------------------------------------
# PluginRegistrations
# ---------------------------------------------------------------------------


def test_plugin_registrations_default_empty() -> None:
    """PluginRegistrations initializes with empty lists."""
    regs = PluginRegistrations()
    assert regs.nodes == []
    assert regs.edges == []
    assert regs.agent_tools == []
    assert regs.triggers == []
    assert regs.listeners == []
    assert regs.module_roots == []


# ---------------------------------------------------------------------------
# register_node
# ---------------------------------------------------------------------------


def test_register_node_success() -> None:
    """register_node adds node to registry and bookkeeping."""
    api, node_reg, *_ = _make_api_with_fresh_registries()
    try:

        class MyNode:
            pass

        meta = NodeMetadata(name="MyTestNode", description="Test node")
        api.register_node(meta, MyNode)

        assert "MyTestNode" in api.registrations.nodes
        assert node_reg.get_node("MyTestNode") is not None
    finally:
        _restore_registries()


def test_register_node_duplicate_raises() -> None:
    """register_node raises ValueError when node is already registered (lines 41-42)."""
    api, node_reg, *_ = _make_api_with_fresh_registries()
    try:

        class MyNode:
            pass

        meta = NodeMetadata(name="DuplicateNode", description="")
        node_reg.register(meta)(MyNode)  # pre-register directly

        with pytest.raises(ValueError, match="DuplicateNode"):
            api.register_node(meta, MyNode)
    finally:
        _restore_registries()


# ---------------------------------------------------------------------------
# register_edge
# ---------------------------------------------------------------------------


def test_register_edge_success() -> None:
    """register_edge adds edge to registry and bookkeeping."""
    api, _, edge_reg, *_ = _make_api_with_fresh_registries()
    try:

        def my_edge(state: Any) -> Any:
            return state

        meta = EdgeMetadata(name="TestEdge", description="A test edge")
        api.register_edge(meta, my_edge)

        assert "TestEdge" in api.registrations.edges
        assert edge_reg.get_edge("TestEdge") is my_edge
    finally:
        _restore_registries()


def test_register_edge_duplicate_raises() -> None:
    """register_edge raises ValueError when edge already exists (lines 54-55)."""
    api, _, edge_reg, *_ = _make_api_with_fresh_registries()
    try:

        def my_edge(state: Any) -> Any:
            return state

        meta = EdgeMetadata(name="ExistingEdge", description="")
        edge_reg.register(meta)(my_edge)  # pre-register

        with pytest.raises(ValueError, match="ExistingEdge"):
            api.register_edge(meta, my_edge)
    finally:
        _restore_registries()


# ---------------------------------------------------------------------------
# register_agent_tool
# ---------------------------------------------------------------------------


def test_register_agent_tool_success() -> None:
    """register_agent_tool adds tool to registry and bookkeeping (lines 68-69)."""
    api, _, _, tool_reg, *_ = _make_api_with_fresh_registries()
    try:

        def tool(query: object) -> object:
            return query

        meta = ToolMetadata(name="my_search", description="Search")
        api.register_agent_tool(meta, tool)

        assert "my_search" in api.registrations.agent_tools
        assert tool_reg.get_tool("my_search") is tool
    finally:
        _restore_registries()


def test_register_agent_tool_duplicate_raises() -> None:
    """register_agent_tool raises ValueError for duplicate (lines 65-67)."""
    api, _, _, tool_reg, *_ = _make_api_with_fresh_registries()
    try:

        def tool() -> None:
            pass

        meta = ToolMetadata(name="existing_tool", description="")
        tool_reg.register(meta)(tool)  # pre-register

        with pytest.raises(ValueError, match="existing_tool"):
            api.register_agent_tool(meta, tool)
    finally:
        _restore_registries()


# ---------------------------------------------------------------------------
# register_trigger
# ---------------------------------------------------------------------------


def test_register_trigger_success() -> None:
    """register_trigger registers trigger and updates bookkeeping."""
    api, _, _, _, trigger_reg, _ = _make_api_with_fresh_registries()
    try:

        def factory(**kw: object) -> object:
            return kw

        meta = TriggerMetadata(id="my-trigger", display_name="My Trigger")
        api.register_trigger(meta, factory)

        assert "my-trigger" in api.registrations.triggers
        assert trigger_reg.get("my-trigger") is not None
    finally:
        _restore_registries()


# ---------------------------------------------------------------------------
# register_listener
# ---------------------------------------------------------------------------


def test_register_listener_success() -> None:
    """register_listener registers platform and updates bookkeeping."""
    api, _, _, _, _, listener_reg = _make_api_with_fresh_registries()
    try:

        def compiler(
            *, workflow_id: Any, workflow_version_id: Any, item: Any, platform_id: Any
        ) -> None:
            return None

        def adapter_factory(
            *, repository: Any, subscription: Any, runtime_id: Any
        ) -> Any:
            return object()

        meta = ListenerMetadata(id="my-platform", display_name="My Platform")
        api.register_listener(meta, compiler, adapter_factory)

        assert "my-platform" in api.registrations.listeners
        assert listener_reg.resolve("my-platform") is not None
    finally:
        _restore_registries()
