"""Routing edges that read values from node results."""

# ruff: noqa: I001

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from pydantic import Field

from orcheo.edges.base import BaseEdge
from orcheo.edges.registry import EdgeMetadata, edge_registry
from orcheo.graph.state import State
from orcheo.runtime.results import node_result


@edge_registry.register(
    EdgeMetadata(
        name="ResultFlagEdge",
        description="Route based on a boolean flag stored in a node result",
        category="logic",
    )
)
class ResultFlagEdge(BaseEdge):
    """Route to one of two branches depending on a result flag."""

    result_node: str
    flag: str = Field(
        default="done",
        description="Name of the boolean-ish field to inspect in the result",
    )
    true_route: str
    false_route: str

    async def run(self, state: State, config: RunnableConfig) -> str:
        """Return the route selected by the flag value."""
        result = node_result(state, self.result_node)
        return self.true_route if bool(result.get(self.flag)) else self.false_route


@edge_registry.register(
    EdgeMetadata(
        name="ResultFieldRouteEdge",
        description="Route based on a named result field",
        category="logic",
    )
)
class ResultFieldRouteEdge(BaseEdge):
    """Route using the value of a result field."""

    result_node: str
    field: str = Field(
        default="routing",
        description="Result field used to select the downstream route",
    )
    allowed_routes: set[str] = Field(
        default_factory=set,
        description="Set of route names allowed through without fallback",
    )
    fallback_route: str = Field(
        default="default",
        description="Route returned when the field value is missing or invalid",
    )

    async def run(self, state: State, config: RunnableConfig) -> str:
        """Return the chosen route, or the fallback when the value is invalid."""
        result = node_result(state, self.result_node)
        route = str(result.get(self.field) or "")
        if route and (not self.allowed_routes or route in self.allowed_routes):
            return route
        return self.fallback_route


__all__ = ["ResultFieldRouteEdge", "ResultFlagEdge"]
