"""Human input interrupt nodes."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="HumanInputNode",
        description="Pause a workflow and collect human input",
        category="logic",
    )
)
class HumanInputNode(TaskNode):
    """Generic human-in-the-loop input node."""

    prompt: str = Field(
        default="Please respond.",
        description="Message shown to the human when the workflow interrupts.",
    )
    kind: str = Field(
        default="human",
        description="Interrupt payload kind for workflow-specific clients.",
    )
    expected: Any = Field(
        default=None,
        description="Optional schema or metadata describing the expected response.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Interrupt with a configured prompt and store the human input."""
        del state, config
        payload: dict[str, Any] = {"message": self.prompt, "kind": self.kind}
        if self.expected is not None:
            payload["expected"] = self.expected
        response = interrupt(payload)
        result: dict[str, Any] = {"response": response}

        message = _response_message_text(response)
        if message is not None:
            result["messages"] = [{"role": "user", "content": message}]

        return result


def _response_message_text(value: Any) -> str | None:
    """Return a chat message string for common interrupt response shapes."""
    if isinstance(value, str):
        candidate = value.strip()
        return candidate or None
    if isinstance(value, int | float | bool):
        return str(value)
    if isinstance(value, Mapping):
        for key in ("message", "text", "content"):
            nested = _response_message_text(value.get(key))
            if nested is not None:
                return nested
    return None


__all__ = ["HumanInputNode"]
