"""WeCom Event Agent workflow with a single tool-using agent.

Configure WeCom to send callback requests to:
`/api/workflows/{workflow_id}/triggers/webhook?preserve_raw_body=true`
so signatures can be verified.

Configurable inputs (workflow_config.json):
- agent_id (WeCom app agent ID for internal messages)
- events_database (MongoDB database for events/RSVPs)
- events_collection (MongoDB collection for events)
- rsvps_collection (MongoDB collection for RSVPs)

Orcheo vault secrets required:
- wecom_corp_id: WeCom corp ID
- wecom_app_secret: WeCom app secret for access token
- wecom_token: Callback token for signature validation
- wecom_encoding_aes_key: AES key for callback decryption
- mdb_connection_string: MongoDB connection string
"""

from collections.abc import Mapping
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from orcheo.edges import Condition, IfElseEdge
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.base import TaskNode
from orcheo.nodes.connectors.wecom import (
    WeComAccessTokenNode,
    WeComCustomerServiceSendNode,
    WeComCustomerServiceSyncNode,
    WeComEventsParserNode,
    WeComSendMessageNode,
)


DEFAULT_MODEL = "openai:gpt-4o-mini"
SYSTEM_PROMPT = (
    "你是一个企业微信活动助理。使用对话历史来推断用户意图，调用工具"
    "读写 MongoDB，并用纯文本回复。不要输出 JSON 或 Markdown。\n\n"
    "上下文：\n"
    "- internal_user_id: {{resolve_attendee_id.internal_user_id}}\n"
    "- external_userid: {{resolve_attendee_id.external_userid}}\n"
    "- attendee_id: {{resolve_attendee_id.attendee_id}}\n\n"
    "MongoDB 配置（在工具调用中使用以下令牌）：\n"
    "- events_database\n"
    "- events_collection\n"
    "- rsvps_collection\n\n"
    "工具：\n"
    "- mongodb_update_one：使用 filter/update/options 调用 update_one。\n"
    "- mongodb_find：使用 filter/sort/limit 调用 find。\n\n"
    "指南：\n"
    "- 用户标识：\n"
    "  - internal_user_id（来自企业微信内部消息）\n"
    "  - external_userid（来自客服同步）\n"
    "- 用户可发送 /重置聊天 重置对话。\n"
    "- 工具调用中必须包含上面配置的 database/collection。\n"
    "- RSVP 更新时，必须同时按 event_id 和 attendee_id 过滤。使用：\n"
    "  - 客服消息：attendee_id = external_userid\n"
    "  - 内部消息：attendee_id = internal_user_id\n"
    "- 取消请求时，将 status 设为 cancelled，且不要新建 RSVP。\n"
    "- 如缺少必填字段，需提出澄清问题。\n"
    "- 日期使用 ISO 8601。\n"
    "- 回复应简洁、面向用户。\n"
    "- 用户首次发送消息时，介绍自己和功能。"
)


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


class ExtractAgentReplyNode(TaskNode):
    """Extract the latest agent reply or fall back to an empty string."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return the agent's latest reply or an empty fallback."""
        reply = None
        messages = state.get("messages")
        if isinstance(messages, list):
            reply = extract_reply_from_messages(messages)
        return {"agent_reply": reply or ""}


class NoOpNode(TaskNode):
    """No-op task node used as a routing placeholder."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return an empty payload to keep routing nodes consistent."""
        return {}


class ResolveAttendeeNode(TaskNode):
    """Resolve attendee identifiers for internal or customer service messages."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Resolve the internal and external attendee identifiers."""
        results = state.get("results", {})
        parser_result = results.get("wecom_events_parser", {})
        cs_sync_result = results.get("wecom_cs_sync", {})

        internal_user_id = ""
        if isinstance(parser_result, Mapping):
            internal_user_id = str(parser_result.get("user", "") or "").strip()

        external_userid = ""
        if isinstance(cs_sync_result, Mapping):
            external_userid = str(
                cs_sync_result.get("external_userid", "") or ""
            ).strip()

        attendee_id = external_userid or internal_user_id
        return {
            "internal_user_id": internal_user_id,
            "external_userid": external_userid,
            "attendee_id": attendee_id,
        }


async def build_graph() -> StateGraph:
    """Build the WeCom event workflow powered by a single agent."""
    graph = StateGraph(State)

    graph.add_node(
        "wecom_events_parser",
        WeComEventsParserNode(
            name="wecom_events_parser",
        ),
    )

    graph.add_node(
        "get_access_token",
        WeComAccessTokenNode(
            name="get_access_token",
        ),
    )

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
        "agent",
        AgentNode(
            name="agent",
            ai_model=DEFAULT_MODEL,
            model_kwargs={"api_key": "[[openai_api_key]]"},
            system_prompt=SYSTEM_PROMPT,
            reset_command="/重置聊天",
            predefined_tools=["mongodb_update_one", "mongodb_find"],
        ),
    )

    graph.add_node(
        "resolve_attendee_id",
        ResolveAttendeeNode(name="resolve_attendee_id"),
    )

    graph.add_node(
        "extract_agent_reply",
        ExtractAgentReplyNode(name="extract_agent_reply"),
    )

    graph.add_node(
        "send_cs_reply",
        WeComCustomerServiceSendNode(
            name="send_cs_reply",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{extract_agent_reply.agent_reply}}",
        ),
    )
    graph.add_node(
        "send_internal_reply",
        WeComSendMessageNode(
            name="send_internal_reply",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{extract_agent_reply.agent_reply}}",
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
            )
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

    graph.add_node("route_by_type", NoOpNode(name="route_by_type"))
    graph.add_conditional_edges(
        "route_by_type",
        message_type_router,
        {
            "true": "get_cs_access_token",
            "false": "get_access_token",
        },
    )

    graph.add_edge("get_access_token", "resolve_attendee_id")
    graph.add_edge("resolve_attendee_id", "agent")

    graph.add_edge("get_cs_access_token", "wecom_cs_sync")
    cs_sync_router = IfElseEdge(
        name="cs_sync_router",
        conditions=[
            Condition(
                left="{{wecom_cs_sync.should_process}}",
                operator="is_falsy",
            )
        ],
    )
    graph.add_conditional_edges(
        "wecom_cs_sync",
        cs_sync_router,
        {
            "true": END,
            "false": "resolve_attendee_id",
        },
    )

    graph.add_edge("agent", "extract_agent_reply")
    graph.add_node("route_reply", NoOpNode(name="route_reply"))
    graph.add_edge("extract_agent_reply", "route_reply")

    reply_channel_router = IfElseEdge(
        name="reply_channel_router",
        conditions=[
            Condition(
                left="{{wecom_events_parser.is_customer_service}}",
                operator="is_truthy",
            )
        ],
    )
    graph.add_conditional_edges(
        "route_reply",
        reply_channel_router,
        {
            "true": "send_cs_reply",
            "false": "send_internal_reply",
        },
    )

    graph.add_edge("send_cs_reply", END)
    graph.add_edge("send_internal_reply", END)

    return graph
