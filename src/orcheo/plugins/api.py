"""Stable registration API exposed to Orcheo plugins."""

from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from orcheo.edges.registry import EdgeMetadata, edge_registry
from orcheo.listeners.registry import (
    ListenerAdapterFactory,
    ListenerCompilerHook,
    ListenerMetadata,
    listener_registry,
)
from orcheo.nodes.agent_tools.registry import ToolMetadata, tool_registry
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.triggers.registry import TriggerMetadata, trigger_registry


@dataclass(slots=True)
class PluginRegistrations:
    """Bookkeeping for plugin-provided registry entries."""

    nodes: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    agent_tools: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    listeners: list[str] = field(default_factory=list)
    module_roots: list[str] = field(default_factory=list)


class PluginAPI:
    """Stable plugin-facing registration surface."""

    def __init__(self) -> None:
        """Initialize empty registration bookkeeping for one plugin load."""
        self.registrations = PluginRegistrations()

    def register_node(self, metadata: NodeMetadata, cls: Callable[..., Any]) -> None:
        """Register a plugin-provided node."""
        if registry.get_node(metadata.name) is not None:
            msg = f"Node '{metadata.name}' is already registered."
            raise ValueError(msg)
        registry.register(metadata)(cls)
        self.registrations.nodes.append(metadata.name)

    def register_edge(
        self,
        metadata: EdgeMetadata,
        cls: Callable[..., Any],
    ) -> None:
        """Register a plugin-provided edge."""
        if edge_registry.get_edge(metadata.name) is not None:
            msg = f"Edge '{metadata.name}' is already registered."
            raise ValueError(msg)
        edge_registry.register(metadata)(cls)
        self.registrations.edges.append(metadata.name)

    def register_agent_tool(
        self, metadata: ToolMetadata, tool: Callable[..., Any] | Any
    ) -> None:
        """Register a plugin-provided agent tool."""
        if tool_registry.get_tool(metadata.name) is not None:
            msg = f"Agent tool '{metadata.name}' is already registered."
            raise ValueError(msg)
        tool_registry.register(metadata)(tool)
        self.registrations.agent_tools.append(metadata.name)

    def register_trigger(
        self,
        metadata: TriggerMetadata,
        factory: Callable[..., Any],
    ) -> None:
        """Register a plugin-provided trigger type."""
        trigger_registry.register(metadata, factory)
        self.registrations.triggers.append(metadata.id)

    def register_listener(
        self,
        metadata: ListenerMetadata,
        compiler: ListenerCompilerHook,
        adapter_factory: ListenerAdapterFactory,
        *,
        aliases: tuple[str, ...] = (),
    ) -> None:
        """Register a plugin-provided listener platform."""
        listener_registry.register(
            metadata,
            compiler=compiler,
            adapter_factory=adapter_factory,
            aliases=aliases,
        )
        self.registrations.listeners.append(metadata.id)


__all__ = ["PluginAPI", "PluginRegistrations"]
