"""WeCom Customer Service echo history workflow.

Configure WeCom to send callback requests to:
`/api/workflows/{workflow_id}/triggers/webhook?preserve_raw_body=true`
so signatures can be verified.

Orcheo vault secrets required:
- wecom_corp_id: WeCom corp ID
- wecom_app_secret: WeCom app secret for access token
- wecom_token: Callback token for signature validation
- wecom_encoding_aes_key: AES key for callback decryption
"""

from collections.abc import Mapping
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from orcheo.edges import Condition, IfElseEdge
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.connectors.wecom import (
    WeComAccessTokenNode,
    WeComCustomerServiceSendNode,
    WeComCustomerServiceSyncNode,
    WeComEventsParserNode,
)


def normalize_role(role: str | None, direction: str | None) -> str:
    """Return a standardized sender label based on role and direction."""
    if role in {"ai", "assistant"}:
        return "assistant"
    if role == "user":
        return "user"
    if direction == "outbound":
        return "assistant"
    if direction == "inbound":
        return "user"
    return "unknown"


class BuildEchoReplyNode(TaskNode):
    """Build a reply that lists the full CS chat history."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Assemble the customer service chat history into a reply."""
        results = state.get("results", {})
        sync_result = results.get("wecom_cs_sync", {})
        history = (
            sync_result.get("messages") if isinstance(sync_result, Mapping) else None
        )

        entries: list[tuple[int, str, str]] = []
        if isinstance(history, list):
            for item in history:
                if not isinstance(item, Mapping):
                    continue
                content = item.get("content")
                if not isinstance(content, str):
                    continue
                cleaned = " ".join(content.splitlines()).strip()
                if not cleaned:
                    continue
                role = normalize_role(item.get("role"), item.get("direction"))
                msgtime = item.get("msgtime", 0)
                if not isinstance(msgtime, int):
                    msgtime = 0
                entries.append((msgtime, role, cleaned))

        entries.sort(key=lambda item: item[0])

        if not entries:
            reply = "No chat history yet."
        else:
            lines = ["Chat history:"]
            index = 1
            for _, role, content in entries:
                lines.append(f"{index}. {role}: {content}")
                index = index + 1
            reply = "\n".join(lines)

        return {"echo_reply": reply}


async def build_graph() -> StateGraph:
    """Build the WeCom CS echo history workflow."""
    graph = StateGraph(State)

    graph.add_node(
        "wecom_events_parser",
        WeComEventsParserNode(
            name="wecom_events_parser",
        ),
    )

    graph.add_node(
        "get_cs_access_token",
        WeComAccessTokenNode(
            name="get_cs_access_token",
            app_secret="[[wecom_app_secret_eventually]]",
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
            message="{{build_echo_reply.echo_reply}}",
        ),
    )

    graph.add_node(
        "build_echo_reply",
        BuildEchoReplyNode(
            name="build_echo_reply",
        ),
    )

    graph.set_entry_point("wecom_events_parser")

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

    message_type_router = IfElseEdge(
        name="message_type_router",
        conditions=[
            Condition(
                left="{{wecom_events_parser.is_customer_service}}",
                operator="is_truthy",
            ),
        ],
    )

    graph.add_conditional_edges(
        "wecom_events_parser",
        immediate_response_router,
        {
            "true": END,
            "false": "route_by_type",
        },
    )

    graph.add_node("route_by_type", lambda _: {})
    graph.add_conditional_edges(
        "route_by_type",
        message_type_router,
        {
            "true": "get_cs_access_token",
            "false": END,
        },
    )

    graph.add_edge("get_cs_access_token", "wecom_cs_sync")

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
            "true": END,
            "false": "build_echo_reply",
        },
    )
    graph.add_edge("build_echo_reply", "wecom_cs_send")
    graph.add_edge("wecom_cs_send", END)

    return graph
