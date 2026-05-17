"""WeCom bot responder workflow for direct messages.

This workflow handles messages from both:
1. Internal WeCom users (企业微信成员) - direct app messages
2. External WeChat users (微信用户) - via Customer Service (微信客服)

Configure WeCom to send callback requests to:
`/api/workflows/{workflow_id}/triggers/webhook?preserve_raw_body=true`
so signatures can be verified.

Configurable inputs (workflow_config.json):
- agent_id (WeCom app agent ID, for internal users)
- reply_message (fixed response content)

Orcheo vault secrets required:
- wecom_corp_id: WeCom corp ID
- wecom_app_secret: WeCom app secret for access token
- wecom_token: Callback token for signature validation
- wecom_encoding_aes_key: AES key for callback decryption
"""

from collections.abc import Mapping
from typing import Any
from langgraph.graph import END, StateGraph
from orcheo.edges import Condition, IfElseEdge
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.connectors.wecom import (
    WeComAccessTokenNode,
    WeComCustomerServiceSendNode,
    WeComCustomerServiceSyncNode,
    WeComEventsParserNode,
    WeComSendMessageNode,
)


DEFAULT_MODEL = "openai:gpt-4o-mini"


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
    """Build the WeCom bot responder workflow."""
    graph = StateGraph(State)

    # Parse the incoming callback event (handles both internal and CS events)
    graph.add_node(
        "wecom_events_parser",
        WeComEventsParserNode(
            name="wecom_events_parser",
        ),
    )

    # --- Internal WeCom user path ---
    graph.add_node(
        "get_access_token",
        WeComAccessTokenNode(
            name="get_access_token",
        ),
    )

    graph.add_node(
        "send_message",
        WeComSendMessageNode(
            name="send_message",
            agent_id="{{config.configurable.agent_id}}",
            message="{{agent_reply}}",
        ),
    )

    # --- External WeChat user path (Customer Service) ---
    # CS nodes require an access token from a preceding WeComAccessTokenNode.
    # We use "get_cs_access_token" to distinguish from the internal user path,
    # but the CS nodes will find tokens from either "get_access_token" or
    # "get_cs_access_token" node results.
    graph.add_node(
        "get_cs_access_token",
        WeComAccessTokenNode(
            name="get_cs_access_token",
        ),
    )

    graph.add_node(
        "wecom_cs_sync",
        WeComCustomerServiceSyncNode(
            name="wecom_cs_sync",
        ),
    )

    graph.add_node(
        "wecom_cs_send",
        WeComCustomerServiceSendNode(
            name="wecom_cs_send",
            message="{{agent_reply}}",
        ),
    )

    graph.add_node(
        "agent",
        AgentNode(
            name="agent",
            ai_model=DEFAULT_MODEL,
            model_kwargs={"api_key": "[[openai_api_key]]"},
            system_prompt=(
                "You are a helpful assistant responding to WeCom users. "
                "Reply concisely, be friendly and professional, and match "
                "the user's language."
            ),
        ),
    )
    graph.add_node("extract_agent_reply", extract_agent_reply)

    # Entry point
    graph.set_entry_point("wecom_events_parser")

    # First router: check if we should stop or continue processing
    # Stop if: immediate_response exists OR should_process is false
    immediate_response_router = IfElseEdge(
        name="immediate_response_router",
        conditions=[
            Condition(
                left="{{wecom_events_parser.immediate_response}}",
                operator="is_truthy",
            ),
            Condition(
                left="{{wecom_events_parser.should_process}}",
                operator="is_falsy",
            ),
        ],
        condition_logic="or",
    )

    # Second router: route to internal or CS path based on is_customer_service
    message_type_router = IfElseEdge(
        name="message_type_router",
        conditions=[
            Condition(
                left="{{wecom_events_parser.is_customer_service}}",
                operator="is_truthy",
            ),
        ],
    )

    # Route from parser: stop, or continue to type-based routing
    graph.add_conditional_edges(
        "wecom_events_parser",
        immediate_response_router,
        {
            "true": END,  # Immediate response or nothing to process
            "false": "route_by_type",  # Continue to message type routing
        },
    )

    # Virtual routing node to split internal vs CS paths
    graph.add_node("route_by_type", lambda _: {})
    graph.add_conditional_edges(
        "route_by_type",
        message_type_router,
        {
            "true": "get_cs_access_token",  # External WeChat user: get token first
            "false": "get_access_token",  # Internal WeCom user: get token
        },
    )

    # --- Internal user path ---
    graph.add_edge("get_access_token", "agent")
    graph.add_edge("agent", "extract_agent_reply")
    graph.add_edge("extract_agent_reply", "send_message")
    graph.add_edge("send_message", END)

    # --- External user (CS) path ---
    graph.add_edge("get_cs_access_token", "wecom_cs_sync")

    # After syncing, check if we have a message to respond to
    cs_sync_router = IfElseEdge(
        name="cs_sync_router",
        conditions=[
            Condition(
                left="{{wecom_cs_sync.should_process}}",
                operator="is_falsy",
            ),
        ],
    )
    graph.add_conditional_edges(
        "wecom_cs_sync",
        cs_sync_router,
        {
            "true": END,  # No message from external user
            "false": "agent",  # Send reply
        },
    )
    graph.add_edge("wecom_cs_send", END)
    graph.add_edge("extract_agent_reply", "wecom_cs_send")

    return graph
