"""WeCom AI bot responder workflow.

Configure WeCom to send AI bot callbacks to:
`/api/workflows/{workflow_id}/triggers/webhook?preserve_raw_body=true`

Configurable inputs (workflow_config.json):
- reply_message (fixed response content)
- reply_msg_type (text, markdown, template_card)
- use_passive_reply (bool, default True)
- receive_id (optional receive_id for encryption validation)

Orcheo vault secrets required:
- wecom_aibot_token: AI bot callback token
- wecom_aibot_encoding_aes_key: AI bot encoding AES key
"""

from collections.abc import Mapping
from typing import Any
from langgraph.graph import END, StateGraph
from orcheo.edges import Condition, IfElseEdge
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.connectors.wecom import (
    WeComAIBotEventsParserNode,
    WeComAIBotPassiveReplyNode,
    WeComAIBotResponseNode,
)


DEFAULT_MODEL = "openai:gpt-4o-mini"


def build_agent_messages(state: State) -> dict[str, Any]:
    """Build a user message list from the WeCom AI bot payload."""
    results = state.get("results", {})
    parser_result = results.get("wecom_ai_bot_events_parser", {})
    content = ""
    if isinstance(parser_result, Mapping):
        content = str(parser_result.get("content", "")).strip()
    if not content:
        return {"messages": []}
    return {"messages": [{"role": "user", "content": content}]}


def extract_reply_from_messages(messages: list[Any]) -> str | None:
    """Return the most recent assistant reply from LangGraph messages."""
    for message in messages[::-1]:
        if isinstance(message, Mapping):
            content = message.get("content")
            role = message.get("type") or message.get("role")
        else:
            content = None
            role = None
            try:
                content = message.content
            except AttributeError:
                content = None
            try:
                role = message.type
            except AttributeError:
                try:
                    role = message.role
                except AttributeError:
                    role = None
        if role in ("ai", "assistant") and isinstance(content, str):
            stripped = content.strip()
            if stripped:
                return stripped
    return None


def extract_agent_reply(state: State) -> dict[str, Any]:
    """Extract the latest agent reply or fall back to configured text."""
    reply = None
    messages = state.get("messages")
    if isinstance(messages, list):
        reply = extract_reply_from_messages(messages)

    if not reply:
        config = state.get("config", {})
        configurable = (
            config.get("configurable", {}) if isinstance(config, dict) else {}
        )
        fallback = configurable.get("reply_message", "")
        if isinstance(fallback, str) and fallback.strip():
            reply = fallback.strip()

    return {"results": {"agent_reply": reply or ""}}


async def build_graph() -> StateGraph:
    """Build the WeCom AI bot responder workflow."""
    graph = StateGraph(State)

    graph.add_node(
        "wecom_ai_bot_events_parser",
        WeComAIBotEventsParserNode(
            name="wecom_ai_bot_events_parser",
            receive_id="{{config.configurable.receive_id}}",
        ),
    )

    graph.add_node(
        "passive_reply",
        WeComAIBotPassiveReplyNode(
            name="passive_reply",
            msg_type="{{config.configurable.reply_msg_type}}",
            content="{{agent_reply}}",
            receive_id="{{config.configurable.receive_id}}",
        ),
    )

    graph.add_node(
        "active_reply",
        WeComAIBotResponseNode(
            name="active_reply",
            msg_type="{{config.configurable.reply_msg_type}}",
            content="{{agent_reply}}",
        ),
    )

    graph.add_node(
        "agent",
        AgentNode(
            name="agent",
            ai_model=DEFAULT_MODEL,
            model_kwargs={"api_key": "[[openai_api_key]]"},
            system_prompt=(
                "You are a WeCom AI bot assistant. Provide concise, helpful "
                "responses in the user's language, formatted for chat."
            ),
        ),
    )

    graph.add_node("build_agent_messages", build_agent_messages)
    graph.add_node("extract_agent_reply", extract_agent_reply)

    graph.set_entry_point("wecom_ai_bot_events_parser")

    immediate_response_router = IfElseEdge(
        name="immediate_response_router",
        conditions=[
            Condition(
                left="{{wecom_ai_bot_events_parser.immediate_response}}",
                operator="is_truthy",
            ),
            Condition(
                left="{{wecom_ai_bot_events_parser.should_process}}",
                operator="is_falsy",
            ),
        ],
        condition_logic="or",
    )

    reply_mode_router = IfElseEdge(
        name="reply_mode_router",
        conditions=[
            Condition(
                left="{{config.configurable.use_passive_reply}}",
                operator="is_truthy",
            )
        ],
    )

    graph.add_conditional_edges(
        "wecom_ai_bot_events_parser",
        immediate_response_router,
        {
            "true": END,
            "false": "build_agent_messages",
        },
    )

    graph.add_edge("build_agent_messages", "agent")
    graph.add_edge("agent", "extract_agent_reply")

    graph.add_node("route_reply_mode", lambda _: {})
    graph.add_edge("extract_agent_reply", "route_reply_mode")
    graph.add_conditional_edges(
        "route_reply_mode",
        reply_mode_router,
        {
            "true": "passive_reply",
            "false": "active_reply",
        },
    )

    graph.add_edge("passive_reply", END)
    graph.add_edge("active_reply", END)

    return graph
