"""Generic routing nodes for agent-directed workflows."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


@registry.register(
    NodeMetadata(
        name="StructuredRouterDispatchNode",
        description="Translate structured agent output into a routing result",
        category="logic",
    )
)
class StructuredRouterDispatchNode(TaskNode):
    """Convert a structured response into a branch route and optional reply."""

    structured_response_key: str = Field(
        default="structured_response",
        description="State key carrying the structured router decision",
    )
    action_field: str = Field(default="action", description="Decision action field")
    route_action_value: str = Field(
        default="route",
        description="Action value that selects a branch route",
    )
    respond_action_value: str = Field(
        default="respond",
        description="Action value that returns a direct reply",
    )
    branch_field: str = Field(default="branch", description="Decision branch field")
    routing_field: str = Field(
        default="routing",
        description="Result field used by downstream routing edges",
    )
    message_field: str = Field(
        default="message",
        description="Decision message field used for direct replies",
    )
    assistant_message_fallback: str = Field(
        default=(
            "How can I help with your qualitative analysis? Upload a CSV file or "
            "transcript, then I'll guide you through generating a codebook."
        ),
        description="Fallback reply when the router omits a message",
    )
    carried_fields: list[str] = Field(
        default_factory=list,
        description="Additional fields copied from the structured decision",
    )

    def _decision_value(self, decision: Any, field: str) -> Any:
        if isinstance(decision, Mapping):
            return decision.get(field)
        if isinstance(decision, BaseModel) and hasattr(decision, field):
            return getattr(decision, field)
        return getattr(decision, field, None)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Resolve the routing decision and persist the selected branch."""
        decision = state.get(self.structured_response_key)
        action = str(self._decision_value(decision, self.action_field) or "").strip()
        branch = str(self._decision_value(decision, self.branch_field) or "").strip()

        nested: dict[str, Any] = {}
        for field in self.carried_fields:
            value = self._decision_value(decision, field)
            if value is not None and value != "":
                nested[field] = value

        if action == self.route_action_value and branch:
            nested[self.routing_field] = branch
            return nested

        message = str(self._decision_value(decision, self.message_field) or "").strip()
        if not message:
            message = self.assistant_message_fallback
        nested[self.routing_field] = self.respond_action_value
        return {"assistant_message": message, **nested}


@registry.register(
    NodeMetadata(
        name="ExtractAIMessageNode",
        description="Extract a text AI message from a structured response",
        category="logic",
    )
)
class ExtractAIMessageNode(TaskNode):
    """Extract a text assistant message from structured agent output."""

    structured_response_key: str = Field(
        default="structured_response",
        description="State key carrying the structured response",
    )
    message_field: str = Field(
        default="assistant_message",
        description="Structured response field carrying the assistant text",
    )
    fallback_message: str = Field(
        default="Sorry, something went wrong. Please try again later.",
        description="Reply used when the structured response omits text",
    )

    def _response_value(self, response: Any, field: str) -> Any:
        if isinstance(response, Mapping):
            return response.get(field)
        if isinstance(response, BaseModel) and hasattr(response, field):
            return getattr(response, field)
        return getattr(response, field, None)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return extracted text as both state and an assistant message."""
        response = state.get(self.structured_response_key)
        reply = self._response_value(response, self.message_field)
        if not isinstance(reply, str) or not reply.strip():
            reply = self.fallback_message
        return {"assistant_message": reply, "messages": [AIMessage(content=reply)]}


__all__ = ["ExtractAIMessageNode", "StructuredRouterDispatchNode"]
