"""Utility nodes such as variable assignment and delays."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


def _build_nested(path: str, value: Any) -> dict[str, Any]:
    """Construct a nested dictionary from a dotted path."""
    if not path:
        msg = "target_path must be a non-empty string"
        raise ValueError(msg)

    segments = [segment.strip() for segment in path.split(".") if segment.strip()]
    if not segments:
        msg = "target_path must contain at least one segment"
        raise ValueError(msg)

    root: dict[str, Any] = {}
    cursor = root
    for segment in segments[:-1]:
        cursor = cursor.setdefault(segment, {})
    cursor[segments[-1]] = value
    return root


@registry.register(
    NodeMetadata(
        name="SetVariableNode",
        description="Store variables for downstream nodes",
        category="utility",
    )
)
class SetVariableNode(TaskNode):
    """Persist multiple variables using a dictionary."""

    variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionary of variables to persist",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return the assigned variables."""
        payload: dict[str, Any] = {}

        def merge(base: dict[str, Any], incoming: Mapping[str, Any]) -> None:
            for key, value in incoming.items():
                if isinstance(value, Mapping):
                    existing = base.get(key)
                    if isinstance(existing, dict):
                        merge(existing, value)
                    else:
                        base[key] = dict(value)
                else:
                    base[key] = value

        for name, value in self.variables.items():
            if "." in name:
                nested = _build_nested(name, value)
                merge(payload, nested)
            else:
                existing = payload.get(name)
                if isinstance(existing, dict) and isinstance(value, Mapping):
                    merge(existing, value)
                elif isinstance(value, Mapping):
                    payload[name] = dict(value)
                else:
                    payload[name] = value

        return payload


@registry.register(
    NodeMetadata(
        name="DelayNode",
        description="Pause execution for a fixed duration",
        category="utility",
    )
)
class DelayNode(TaskNode):
    """Introduce an asynchronous delay within the workflow."""

    duration_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Duration of the pause expressed in seconds",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Sleep for the requested duration and return timing metadata."""
        await asyncio.sleep(self.duration_seconds)
        return {
            "duration_seconds": self.duration_seconds,
        }


@registry.register(
    NodeMetadata(
        name="ForLoopNode",
        description="Iterate over a list, exposing one item per invocation",
        category="utility",
    )
)
class ForLoopNode(TaskNode):
    """Loop controller that advances through a list one item at a time.

    On each invocation the node reads its own previous output from
    ``state["node_results"][self.name]`` to determine the current index,
    exposes the item at that index as ``current_item``, and stores the
    *next* index so subsequent invocations advance automatically.

    Pair with an :class:`~orcheo.edges.branching.IfElseEdge` edge that
    routes on ``done`` to build a complete for-loop::

        graph.add_node("for_each", ForLoopNode(
            name="for_each",
            items="{{find_subscribers.data}}",
        ))
        router = IfElseEdge(
            name="for_each_router",
            conditions=[Condition(
                left="{{for_each.done}}", operator="is_falsy",
            )],
        )
        graph.add_conditional_edges(
            "for_each", router,
            {"true": "loop_body", "false": END},
        )
        graph.add_edge("loop_body", "for_each")
    """

    items: list[Any] | str = Field(
        description="List to iterate over (may be a template string)",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return the current item and advance the index."""
        items = self.items
        if not isinstance(items, list):
            return {"done": True, "index": 0, "total": 0, "current_item": None}

        total = len(items)

        # Read the index left by the previous invocation.
        prev = state.get("node_results", {})
        if isinstance(prev, Mapping):
            loop_state = prev.get(self.name, {})
        else:
            loop_state = {}
        if isinstance(loop_state, Mapping):
            raw_index = loop_state.get("index", 0)
            index = int(raw_index) if raw_index is not None else 0
        else:
            index = 0

        if index >= total:
            return {
                "done": True,
                "index": index,
                "iteration": index,
                "total": total,
                "current_item": None,
            }

        return {
            "done": False,
            "index": index + 1,
            "iteration": index + 1,
            "total": total,
            "current_item": items[index],
        }


__all__ = [
    "SetVariableNode",
    "DelayNode",
    "ForLoopNode",
]
