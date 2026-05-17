"""Nodes useful for debugging workflow execution."""

from __future__ import annotations
import logging
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.nodes.state_utils import extract_from_state, normalise_state_snapshot


logger = logging.getLogger(__name__)


@registry.register(
    NodeMetadata(
        name="DebugNode",
        description="Capture state snapshots and emit debug information.",
        category="utility",
    )
)
class DebugNode(TaskNode):
    """Emit debug information without mutating workflow state."""

    message: str | None = Field(
        default=None,
        description="Optional message recorded alongside the snapshot",
    )
    tap_path: str | None = Field(
        default=None,
        description="Dotted path resolved from state['results'] for inspection",
    )
    include_state: bool = Field(
        default=False,
        description="Whether to include the state snapshot in the output",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return debugging metadata for the current execution context."""
        found = False
        tapped_value: Any = None
        if self.tap_path is not None:
            found, tapped_value = extract_from_state(state, self.tap_path)

        log_components: list[str] = []
        if self.message:
            log_components.append(self.message)
        if self.tap_path:
            log_components.append(
                f"path={self.tap_path} found={found} value={tapped_value!r}"
            )
        if log_components:
            logger.info("DebugNode %s: %s", self.name, " | ".join(log_components))

        payload: dict[str, Any] = {
            "message": self.message,
            "tap_path": self.tap_path,
            "found": found,
            "value": tapped_value,
        }
        if self.include_state:
            payload["state"] = normalise_state_snapshot(state)
        return payload


__all__ = ["DebugNode"]
