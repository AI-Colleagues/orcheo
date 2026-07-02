"""Graph module for Orcheo.

Re-exports the LangGraph graph-construction symbols (:class:`StateGraph`,
:data:`START`, :data:`END`) so that conforming ``workflow.py`` scripts can source
them from Orcheo only. The restricted grammar (see
:mod:`orcheo.graph.ir.grammar`) allows imports from ``orcheo`` exclusively; this
re-export lets the "Orcheo-only import" rule hold without a ``langgraph``
exception.
"""

from __future__ import annotations
from collections.abc import Awaitable, Callable, Hashable, Mapping, Sequence
from typing import Any, Self
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.graph import END, START
from langgraph.graph import StateGraph as _LangGraphStateGraph


class StateGraph(_LangGraphStateGraph):
    """StateGraph with Orcheo declarative conditional-edge support."""

    def add_conditional_edges(
        self,
        source: str,
        path: (
            Callable[..., Hashable | Sequence[Hashable]]
            | Callable[..., Awaitable[Hashable | Sequence[Hashable]]]
            | Runnable[Any, Hashable | Sequence[Hashable]]
            | Mapping[str, Any]
        ),
        path_map: dict[Hashable, str] | list[str] | None = None,
    ) -> Self:
        """Add conditional edges, accepting Orcheo's declarative config shape."""
        if isinstance(path, Mapping):
            from orcheo.graph.conditional import add_conditional_edges

            add_conditional_edges(self, {"source": source, **dict(path)}, {})
            return self

        return super().add_conditional_edges(source, path, path_map)


__all__ = ["END", "START", "StateGraph", "RunnableConfig"]
