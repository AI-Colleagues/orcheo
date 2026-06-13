"""Edge system for Orcheo workflow routing.

This module provides edges for conditional routing and branching logic.
Edges handle routing decisions, while nodes handle data transformations.
"""

# ruff: noqa: I001

from orcheo.edges.base import BaseEdge
from orcheo.edges.branching import (
    IfElseEdge,
    SwitchCase,
    SwitchEdge,
    WhileEdge,
)
from orcheo.edges.results import ResultFieldRouteEdge, ResultFlagEdge
from orcheo.edges.conditions import ComparisonOperator, Condition
from orcheo.edges.registry import EdgeMetadata, EdgeRegistry, edge_registry


__all__ = [
    "BaseEdge",
    "IfElseEdge",
    "SwitchEdge",
    "WhileEdge",
    "ResultFlagEdge",
    "ResultFieldRouteEdge",
    "SwitchCase",
    "Condition",
    "ComparisonOperator",
    "EdgeMetadata",
    "EdgeRegistry",
    "edge_registry",
]
