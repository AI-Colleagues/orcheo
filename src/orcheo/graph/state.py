"""Graph state for the workflow."""

from __future__ import annotations
from typing import Annotated, Any
from langgraph.graph import MessagesState


class State(MessagesState):
    """State for the graph."""

    tenant_id: str | None = None
    inputs: dict[str, Any]
    results: Annotated[dict[str, Any], dict_reducer]
    structured_response: Any
    config: dict[str, Any] | None


def dict_reducer(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Reducer for dictionaries."""
    return {**left, **right}
