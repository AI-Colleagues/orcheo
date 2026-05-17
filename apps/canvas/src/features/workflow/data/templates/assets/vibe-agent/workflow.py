"""Canvas template routing Orcheo Vibe requests to external agent runtimes."""

from collections.abc import Mapping, Sequence
from typing import Any
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from orcheo.edges.branching import SwitchCase, SwitchEdge
from orcheo.graph.state import State
from orcheo.nodes.ai.external.claude_code import ClaudeCodeNode
from orcheo.nodes.ai.external.codex import CodexNode
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


def extract_context(inputs: Mapping[str, Any]) -> str:
    """Return the Canvas context string carried in ChatKit metadata."""
    metadata = inputs.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    context = metadata.get("context")
    return context.strip() if isinstance(context, str) else ""


def build_conversation_lines(inputs: Mapping[str, Any]) -> list[str]:
    """Build ordered role: content lines from history and the current message."""
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

    return lines


def with_context(context: str, label: str, body: str) -> str:
    """Prepend a Canvas context block to *body* under *label*."""
    return f"Canvas context:\n{context}\n\n{label}:\n{body}"


def fallback_prompt(inputs: Mapping[str, Any], context: str) -> str:
    """Return the first non-empty scalar prompt field, optionally prefixed with context."""  # noqa: E501
    for key in ("prompt", "query", "input", "message"):
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            return with_context(context, "Task", text) if context else text
    return f"Canvas context:\n{context}" if context else ""


def flatten_inputs(inputs: Mapping[str, Any]) -> str:
    """Flatten Canvas context, chat history, and the current message into one prompt."""
    context = extract_context(inputs)
    conversation = "\n\n".join(build_conversation_lines(inputs))
    if conversation and context:
        return with_context(context, "Conversation", conversation)
    if conversation:
        return conversation
    return fallback_prompt(inputs, context)


class FlattenChatPromptNode(TaskNode):
    """Build one prompt from ChatKit history plus the latest message."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Run the node and return the flattened prompt."""
        del config
        inputs = state.get("inputs", {})
        if not isinstance(inputs, Mapping):
            return {"prompt": ""}
        return {"prompt": flatten_inputs(inputs)}


class ExtractExternalAgentReplyNode(TaskNode):
    """Extract the latest agent output into a ChatKit-visible assistant message."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Run the node and publish results as messages if possible."""
        del config
        results = state.get("results", {})
        if not isinstance(results, Mapping):
            return {"text": ""}

        for source_result_key in ("claude_code_agent", "codex_agent", "gemini_agent"):
            payload = results.get(source_result_key, {})
            if not isinstance(payload, Mapping):
                continue
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
    """Build the Orcheo Vibe template workflow."""
    graph = StateGraph(State)
    graph.add_node("prepare_prompt", FlattenChatPromptNode(name="prepare_prompt"))
    graph.add_node(
        "claude_code_agent",
        ClaudeCodeNode(
            name="claude_code_agent",
            prompt="{{prepare_prompt.prompt}}",
            working_directory="{{config.configurable.working_directory}}",
            timeout_seconds=1200,
        ),
    )
    graph.add_node(
        "codex_agent",
        CodexNode(
            name="codex_agent",
            prompt="{{prepare_prompt.prompt}}",
            working_directory="{{config.configurable.working_directory}}",
            timeout_seconds=1800,
        ),
    )
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
        "extract_reply",
        ExtractExternalAgentReplyNode(name="extract_reply"),
    )

    graph.add_edge(START, "prepare_prompt")
    graph.add_conditional_edges(
        "prepare_prompt",
        SwitchEdge(
            name="provider_route",
            value="{{inputs.model}}",
            cases=[
                SwitchCase(match="claude_code", branch_key="claude_code"),
                SwitchCase(match="codex", branch_key="codex"),
                SwitchCase(match="gemini", branch_key="gemini"),
            ],
            default_branch_key="codex",
        ),
        {
            "claude_code": "claude_code_agent",
            "codex": "codex_agent",
            "gemini": "gemini_agent",
        },
    )
    graph.add_edge("claude_code_agent", "extract_reply")
    graph.add_edge("codex_agent", "extract_reply")
    graph.add_edge("gemini_agent", "extract_reply")
    graph.add_edge("extract_reply", END)
    return graph
