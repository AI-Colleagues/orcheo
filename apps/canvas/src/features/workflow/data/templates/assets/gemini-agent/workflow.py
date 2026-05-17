"""Canvas template running Gemini CLI from flattened ChatKit inputs."""

from collections.abc import Mapping, Sequence
from typing import Any
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.ai.external.gemini import GeminiNode
from orcheo.nodes.base import TaskNode


def stringify_content(value: Any) -> str:
    """Flatten ChatKit content payloads into plain text."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        text = value.get("text")
        return text.strip() if isinstance(text, str) else ""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts = [stringify_content(item) for item in value]
        return "\n".join(part for part in parts if part)
    return ""


def flatten_inputs(inputs: Mapping[str, Any]) -> str:
    """Flatten chat history and the current user message into one prompt."""
    lines: list[str] = []
    history = inputs.get("history")
    if isinstance(history, list):
        for item in history:
            if not isinstance(item, Mapping):
                continue
            role = item.get("role")
            content = stringify_content(item.get("content"))
            if not isinstance(role, str) or not content:
                continue
            lines.append(f"{role.strip()}: {content}")

    message = inputs.get("message")
    if isinstance(message, str) and message.strip():
        latest_user_message = f"user: {message.strip()}"
        if not lines or lines[-1] != latest_user_message:
            lines.append(latest_user_message)

    if lines:
        return "\n\n".join(lines)

    for key in ("prompt", "query", "input", "message"):
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


class FlattenChatPromptNode(TaskNode):
    """Build one Gemini prompt from ChatKit history plus the latest message."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Run the node and return the flattened prompt."""
        del config
        inputs = state.get("inputs", {})
        if not isinstance(inputs, Mapping):
            return {"prompt": ""}
        return {"prompt": flatten_inputs(inputs)}


class PublishExternalAgentReplyNode(TaskNode):
    """Convert external-agent stdout into ChatKit-visible assistant messages."""

    source_result_key: str

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Run the node and publish results as messages if possible."""
        del config
        results = state.get("results", {})
        payload = (
            results.get(self.source_result_key, {}) if isinstance(results, dict) else {}
        )
        if not isinstance(payload, Mapping):
            return {"text": ""}

        for key in ("stdout", "stderr", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return {"text": value.strip()}
        return {"text": ""}

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Run the node and publish results as messages if possible."""
        runnable = self.resolved_for_run(state, config=config)
        result = await runnable.run(state, config)
        output: dict[str, Any] = {"results": {self.name: result}}
        if isinstance(result, Mapping):
            text = result.get("text")
            if isinstance(text, str) and text.strip():
                output["messages"] = [AIMessage(content=text.strip())]
        return output


def build_graph() -> StateGraph:
    """Build the Gemini template workflow."""
    graph = StateGraph(State)
    graph.add_node("prepare_prompt", FlattenChatPromptNode(name="prepare_prompt"))
    graph.add_node(
        "gemini_agent",
        GeminiNode(
            name="gemini_agent",
            prompt="{{prepare_prompt.prompt}}",
            working_directory="{{config.configurable.working_directory}}",
            timeout_seconds=1800,
        ),
    )
    graph.add_node(
        "publish_reply",
        PublishExternalAgentReplyNode(
            name="publish_reply",
            source_result_key="gemini_agent",
        ),
    )

    graph.add_edge(START, "prepare_prompt")
    graph.add_edge("prepare_prompt", "gemini_agent")
    graph.add_edge("gemini_agent", "publish_reply")
    graph.add_edge("publish_reply", END)
    return graph
