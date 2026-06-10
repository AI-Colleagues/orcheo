"""Branching logic edges built on shared condition helpers."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any, Literal
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from orcheo.edges.base import BaseEdge
from orcheo.edges.conditions import (
    Condition,
    _combine_condition_results,
    _normalise_case,
)
from orcheo.edges.registry import EdgeMetadata, edge_registry
from orcheo.graph.state import State


class SwitchCase(BaseModel):
    """Configuration describing an individual switch branch."""

    match: Any | None = Field(
        default=None, description="Value that activates this branch"
    )
    label: str | None = Field(
        default=None, description="Optional label used in the workflow page"
    )
    branch_key: str | None = Field(
        default=None,
        description="Identifier emitted when this branch is selected",
    )
    case_sensitive: bool | None = Field(
        default=None,
        description="Override case-sensitivity for this branch",
    )


def _coerce_branch_key(candidate: str | None, fallback: str) -> str:
    """Return a normalised branch identifier."""
    if candidate:
        candidate = candidate.strip()
    if candidate:
        return candidate
    slug = fallback.strip().lower().replace(" ", "_")
    slug = "".join(char for char in slug if char.isalnum() or char in {"_", "-"})
    return slug or fallback


@edge_registry.register(
    EdgeMetadata(
        name="IfElseEdge",
        description="Branch execution based on a condition",
        category="logic",
    )
)
class IfElseEdge(BaseEdge):
    """Evaluate a boolean expression and emit the chosen branch."""

    conditions: list[Condition] = Field(
        default_factory=lambda: [Condition(left=True, operator="is_truthy")],
        min_length=1,
        description="Collection of conditions that control branching",
    )
    condition_logic: Literal["and", "or"] = Field(
        default="and",
        description="Combine conditions using logical AND/OR semantics",
    )

    async def run(self, state: State, config: RunnableConfig) -> str:
        """Return the evaluated branch key."""
        outcome, _evaluations = _combine_condition_results(
            conditions=self.conditions,
            combinator=self.condition_logic,
        )
        branch = "true" if outcome else "false"
        return branch


@edge_registry.register(
    EdgeMetadata(
        name="SwitchEdge",
        description="Resolve a case key for downstream branching",
        category="logic",
    )
)
class SwitchEdge(BaseEdge):
    """Map an input value to a branch identifier."""

    value: Any = Field(description="Value to inspect for routing decisions")
    case_sensitive: bool = Field(
        default=True,
        description="Preserve case when deriving branch keys",
    )
    default_branch_key: str = Field(
        default="default",
        description="Branch identifier returned when no cases match",
    )
    cases: list[SwitchCase] = Field(
        default_factory=list,
        min_length=1,
        description="Collection of matchable branches",
    )

    def _resolve_case(
        self, case: SwitchCase, *, index: int, normalised_value: Any
    ) -> tuple[str, bool]:
        case_sensitive = (
            case.case_sensitive
            if case.case_sensitive is not None
            else self.case_sensitive
        )
        branch_key = _coerce_branch_key(
            case.branch_key,
            fallback=f"case_{index + 1}",
        )
        expected = _normalise_case(
            case.match,
            case_sensitive=case_sensitive,
        )
        is_match = normalised_value == expected
        return branch_key, is_match

    async def run(self, state: State, config: RunnableConfig) -> str:
        """Return the branch key for routing."""
        raw_value = self.value
        processed = _normalise_case(raw_value, case_sensitive=self.case_sensitive)
        branch_key = self.default_branch_key

        for index, case in enumerate(self.cases):
            candidate_branch, is_match = self._resolve_case(
                case,
                index=index,
                normalised_value=processed,
            )
            if is_match:
                branch_key = candidate_branch
                break

        return branch_key


@edge_registry.register(
    EdgeMetadata(
        name="WhileEdge",
        description="Emit a continue signal while the condition holds",
        category="logic",
    )
)
class WhileEdge(BaseEdge):
    """Evaluate a condition and loop until it fails or a limit is reached.

    The iteration counter is read from ``state["results"][self.name]``
    which must be maintained by a node in the loop body (e.g. a
    :class:`~orcheo.nodes.logic.ForLoopNode` or a custom counter node
    whose ``name`` matches this edge's ``name``).

    When no ``left`` operand is specified on the conditions, the current
    iteration count is used as the default left-hand value, enabling
    concise ``iteration < total`` style checks.
    """

    conditions: list[Condition] = Field(
        default_factory=lambda: [Condition(operator="less_than")],
        min_length=1,
        description="Collection of conditions that control continuation",
    )
    condition_logic: Literal["and", "or"] = Field(
        default="and",
        description="Combine conditions using logical AND/OR semantics",
    )
    max_iterations: int | None = Field(
        default=None,
        ge=1,
        description="Optional guard to stop after this many iterations",
    )

    def _current_iteration(self, state: State) -> int:
        """Return the iteration count persisted in the workflow state.

        The value is expected at ``results[self.name]["iteration"]``
        (written by a companion node such as :class:`ForLoopNode`).
        """
        results = state.get("results")
        if isinstance(results, Mapping):
            edge_state = results.get(self.name)
            if isinstance(edge_state, Mapping):
                iteration = edge_state.get("iteration")
                if isinstance(iteration, int) and iteration >= 0:
                    return iteration
        return 0

    async def run(self, state: State, config: RunnableConfig) -> str:
        """Return ``"continue"`` or ``"exit"`` based on the condition."""
        iteration = self._current_iteration(state)
        outcome, _evaluations = _combine_condition_results(
            conditions=self.conditions,
            combinator=self.condition_logic,
            default_left=iteration,
        )
        should_continue = outcome

        if self.max_iterations is not None and iteration >= self.max_iterations:
            should_continue = False

        return "continue" if should_continue else "exit"


__all__ = [
    "SwitchCase",
    "IfElseEdge",
    "SwitchEdge",
    "WhileEdge",
]
