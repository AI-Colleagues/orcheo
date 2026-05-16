"""WeCom Event Agent workflow for Customer Service messages.

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

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from orcheo.edges import Condition, IfElseEdge
from orcheo.graph.state import State
from orcheo.nodes.ai import AgentNode
from orcheo.nodes.base import TaskNode
from orcheo.nodes.integrations.databases.mongodb import (
    MongoDBFindNode,
    MongoDBNode,
)
from orcheo.nodes.wecom import (
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
        """Return an empty payload for routing placeholders."""
        return {}


class ParseCommandNode(TaskNode):
    """Parse agent JSON output into a command payload."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Parse the agent's JSON reply into structured action data."""
        results = state.get("results", {})
        agent_reply = ""
        if isinstance(results, Mapping):
            reply_result = results.get("extract_agent_reply", {})
            if isinstance(reply_result, Mapping):
                agent_reply = str(reply_result.get("agent_reply", ""))
        agent_reply = agent_reply.strip()

        if not agent_reply:
            return {
                "action": "unknown",
                "event": {},
                "rsvp": {},
                "error": "Empty agent reply",
            }

        try:
            data = json.loads(agent_reply)
        except json.JSONDecodeError:
            return {
                "action": "unknown",
                "event": {},
                "rsvp": {},
                "error": "Agent reply is not valid JSON",
            }

        if not isinstance(data, dict):
            return {
                "action": "unknown",
                "event": {},
                "rsvp": {},
                "error": "Agent reply JSON must be an object",
            }

        action = str(data.get("action", "unknown")).strip().lower() or "unknown"
        event = data.get("event") if isinstance(data.get("event"), dict) else {}
        rsvp = data.get("rsvp") if isinstance(data.get("rsvp"), dict) else {}
        return {"action": action, "event": event, "rsvp": rsvp, "error": None}


class PrepareEventUpdateNode(TaskNode):
    """Prepare MongoDB update payload for an event."""

    @staticmethod
    def coerce_host(host_value: Any) -> dict[str, str]:
        """Normalize host information into a trimmed dict with allowed keys."""
        if isinstance(host_value, dict):
            host = {
                key: str(value).strip()
                for key, value in host_value.items()
                if key in {"name", "id", "email"} and str(value).strip()
            }
            return host
        if isinstance(host_value, str) and host_value.strip():
            return {"name": host_value.strip()}
        return {}

    @staticmethod
    def resolve_requester_id(results: Mapping) -> str:
        """Resolve requester ID from internal or customer service messages."""
        parser_result = results.get("wecom_events_parser", {})
        cs_sync_result = results.get("wecom_cs_sync", {})

        internal_user_id = ""
        if isinstance(parser_result, Mapping):
            internal_user_id = str(
                parser_result.get("user") or parser_result.get("target_user") or ""
            ).strip()

        external_userid = ""
        if isinstance(cs_sync_result, Mapping):
            external_userid = str(cs_sync_result.get("external_userid") or "").strip()

        return external_userid or internal_user_id

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Validate parsed event data and prepare a MongoDB update payload."""
        results = state.get("results", {})
        parse_result = results.get("parse_command", {})

        requester_id = (
            self.resolve_requester_id(results) if isinstance(results, Mapping) else ""
        )
        if not requester_id:
            return {
                "is_valid": False,
                "reply_message": "无法识别请求者，请稍后再试。",
            }

        event_data = self.normalize_event(parse_result)
        missing = self.missing_fields(event_data)
        if missing:
            missing_str = ", ".join(missing)
            return {
                "is_valid": False,
                "reply_message": (f"请提供以下活动信息：{missing_str}。"),
            }

        event_id, generated_id = self.resolve_event_id(event_data)
        if not event_id:
            return {
                "is_valid": False,
                "reply_message": "请提供 event_id 以结束活动。",
            }

        now = datetime.now(UTC).isoformat()
        update_doc = self.build_update_doc(event_data, event_id, requester_id, now)

        return {
            "is_valid": True,
            "event_id": event_id,
            "filter": {"event_id": event_id},
            "update": update_doc,
            "title": event_data["title"],
            "description": event_data["description"],
            "iso_date": event_data["iso_date"],
            "location": event_data["location"],
            "host": event_data["host"],
            "generated_id": generated_id,
            "requester_id": requester_id,
            "is_end_request": event_data["is_end_request"],
            "reply_message": "",
        }

    def normalize_event(self, parse_result: Any) -> dict[str, Any]:
        """Extract normalized event data from the parsed command result."""
        event = (
            parse_result.get("event", {}) if isinstance(parse_result, Mapping) else {}
        )
        action = (
            str(parse_result.get("action", "")).strip().lower()
            if isinstance(parse_result, Mapping)
            else ""
        )
        status_raw = str(event.get("status", "")).strip().lower()
        return {
            "action": action,
            "is_end_request": action == "end_event" or status_raw in {"ended", "end"},
            "status_raw": status_raw,
            "event_id": str(event.get("event_id", "")).strip(),
            "title": str(event.get("title", "")).strip(),
            "description": str(event.get("description", "")).strip(),
            "iso_date": str(event.get("iso_date") or event.get("date") or "").strip(),
            "location": str(event.get("location", "")).strip(),
            "host": self.coerce_host(event.get("host")),
        }

    def missing_fields(self, event_data: Mapping[str, Any]) -> list[str]:
        """List any required event fields that are missing when not ending an event."""
        if event_data["is_end_request"]:
            return []
        missing: list[str] = []
        for key in ("title", "description", "iso_date", "location"):
            if not event_data[key]:
                missing.append(key)
        if not event_data["host"]:
            missing.append("host")
        return missing

    def resolve_event_id(self, event_data: Mapping[str, Any]) -> tuple[str, bool]:
        """Ensure an event ID exists, generating one if needed for new events."""
        event_id = event_data["event_id"]
        generated_id = False
        if not event_id and not event_data["is_end_request"]:
            event_id = str(uuid.uuid4())
            generated_id = True
        return event_id, generated_id

    def build_update_doc(
        self,
        event_data: Mapping[str, Any],
        event_id: str,
        requester_id: str,
        now: str,
    ) -> dict[str, Any]:
        """Construct the MongoDB update payload based on the event details."""
        update_fields: dict[str, Any] = {
            "event_id": event_id,
            "updated_at": now,
        }
        if event_data["is_end_request"]:
            update_fields.update(
                {
                    "status": "ended",
                    "ended_at": now,
                }
            )
        else:
            update_fields.update(
                {
                    "title": event_data["title"],
                    "description": event_data["description"],
                    "iso_date": event_data["iso_date"],
                    "location": event_data["location"],
                    "host": event_data["host"],
                }
            )
        return {
            "$set": update_fields,
            "$setOnInsert": {"created_at": now, "creator_id": requester_id},
        }


class PrepareRsvpUpdateNode(TaskNode):
    """Prepare MongoDB update payload for an RSVP."""

    STATUS_MAP: ClassVar[dict[str, str]] = {
        "yes": "yes",
        "y": "yes",
        "going": "yes",
        "accept": "yes",
        "accepted": "yes",
        "no": "no",
        "n": "no",
        "decline": "no",
        "declined": "no",
        "maybe": "maybe",
        "tentative": "maybe",
        "cancel": "cancelled",
        "canceled": "cancelled",
        "cancelled": "cancelled",
    }

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Validate RSVP data and prepare a MongoDB update payload."""
        results = state.get("results", {})
        parse_result = results.get("parse_command", {})
        rsvp = parse_result.get("rsvp", {}) if isinstance(parse_result, dict) else {}
        cs_result = results.get("wecom_cs_sync", {})

        event_id = str(
            rsvp.get("event_id") or parse_result.get("event_id") or ""
        ).strip()
        attendee_id = str(
            rsvp.get("attendee_id")
            or (cs_result.get("external_userid") if isinstance(cs_result, dict) else "")
            or ""
        ).strip()
        attendee_name = str(rsvp.get("attendee_name", "")).strip()
        external_username_raw = (
            cs_result.get("external_username") if isinstance(cs_result, dict) else ""
        )
        external_username = ""
        if external_username_raw not in (None, "None"):
            external_username = str(external_username_raw).strip()
        if not attendee_name and external_username:
            attendee_name = external_username
        status_raw = str(rsvp.get("status", "")).strip().lower()
        status = self.STATUS_MAP.get(status_raw, status_raw)

        missing = []
        if not event_id:
            missing.append("event_id")
        if not attendee_id:
            missing.append("attendee_id")
        if not status:
            missing.append("status")

        if status and status not in {"yes", "no", "maybe", "cancelled"}:
            return {
                "is_valid": False,
                "reply_message": ("RSVP 状态必须是 yes、no、maybe 或 cancelled。"),
            }

        if missing:
            missing_str = ", ".join(missing)
            return {
                "is_valid": False,
                "reply_message": (f"请提供 RSVP 详情：{missing_str}。"),
            }

        now = datetime.now(UTC).isoformat()
        update_fields: dict[str, Any] = {
            "event_id": event_id,
            "attendee_id": attendee_id,
            "status": status,
            "updated_at": now,
        }
        if attendee_name:
            update_fields["attendee_name"] = attendee_name
        if external_username:
            update_fields["external_username"] = external_username
        update_doc = {"$set": update_fields, "$setOnInsert": {"created_at": now}}

        return {
            "is_valid": True,
            "event_id": event_id,
            "attendee_id": attendee_id,
            "attendee_name": attendee_name,
            "external_username": external_username,
            "status": status,
            "filter": {"event_id": event_id, "attendee_id": attendee_id},
            "update": update_doc,
            "reply_message": "",
        }


class PrepareGetRsvpsNode(TaskNode):
    """Prepare parameters to fetch RSVPs for an event."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Ensure an event_id is present before querying RSVPs."""
        results = state.get("results", {})
        parse_result = results.get("parse_command", {})
        event = parse_result.get("event", {}) if isinstance(parse_result, dict) else {}
        rsvp = parse_result.get("rsvp", {}) if isinstance(parse_result, dict) else {}

        event_id = str(
            event.get("event_id")
            or rsvp.get("event_id")
            or parse_result.get("event_id")
            or ""
        ).strip()

        if not event_id:
            return {
                "is_valid": False,
                "reply_message": "请提供 event_id 以查询 RSVP。",
            }

        return {"is_valid": True, "event_id": event_id, "reply_message": ""}


class PrepareGetEventNode(TaskNode):
    """Prepare parameters to fetch event details."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Ensure an event_id is present before querying event details."""
        results = state.get("results", {})
        parse_result = results.get("parse_command", {})
        event = parse_result.get("event", {}) if isinstance(parse_result, dict) else {}

        event_id = str(
            event.get("event_id") or parse_result.get("event_id") or ""
        ).strip()

        if not event_id:
            return {
                "is_valid": False,
                "reply_message": "请提供 event_id 以查询活动详情。",
            }

        return {"is_valid": True, "event_id": event_id, "reply_message": ""}


class PrepareListEventsNode(TaskNode):
    """Prepare parameters to fetch recent events."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return default parameters for listing recent events."""
        now = datetime.now(UTC).isoformat()
        return {"limit": 20, "now": now}


class ValidateEventOwnerNode(TaskNode):
    """Validate that the requester can update or end the event."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Verify the requester is allowed to update or end the event."""
        results = state.get("results", {})
        prepare_result = results.get("prepare_event", {})
        if not isinstance(prepare_result, Mapping):
            return {
                "is_valid": False,
                "reply_message": "活动信息缺失，请稍后再试。",
            }

        requester_id = str(prepare_result.get("requester_id", "")).strip()
        if not requester_id:
            return {
                "is_valid": False,
                "reply_message": "无法识别请求者，请稍后再试。",
            }

        find_result = results.get("find_event_for_update", {})
        data = find_result.get("data") if isinstance(find_result, Mapping) else None
        existing = data[0] if isinstance(data, list) and data else None

        creator_id = ""
        if isinstance(existing, Mapping):
            creator_id = str(existing.get("creator_id", "") or "").strip()

        if existing and creator_id and creator_id != requester_id:
            return {
                "is_valid": False,
                "reply_message": "只有活动创建者可以更新或结束该活动。",
            }

        if prepare_result.get("is_end_request") and not existing:
            return {
                "is_valid": False,
                "reply_message": "未找到活动，无法结束。",
            }

        return {
            "is_valid": True,
            "reply_message": "",
            "event_exists": bool(existing),
        }


class FormatEventErrorNode(TaskNode):
    """Format error replies for event updates."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return the first event error message to send back to the user."""
        results = state.get("results", {})
        for key in ("validate_event_owner", "prepare_event"):
            payload = results.get(key, {})
            if isinstance(payload, Mapping):
                message = str(payload.get("reply_message", "")).strip()
                if message:
                    return {"message": message}
        return {"message": "活动处理失败，请稍后再试。"}


class FormatEventReplyNode(TaskNode):
    """Format reply after an event update."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Build the confirmation message after saving the event."""
        results = state.get("results", {})
        payload = results.get("prepare_event", {})

        title = payload.get("title") or "未命名活动"
        event_id = payload.get("event_id") or ""
        iso_date = payload.get("iso_date") or ""
        location = payload.get("location") or ""
        host = payload.get("host") or {}
        is_end_request = bool(payload.get("is_end_request"))

        lines = []
        if is_end_request:
            if event_id:
                lines.append(f"活动已结束（ID：{event_id}）。")
            else:
                lines.append("活动已结束。")
            return {"message": "\n".join(lines)}
        if event_id:
            lines.append(f"活动已保存：{title}（ID：{event_id}）。")
        else:
            lines.append(f"活动已保存：{title}。")
        if payload.get("generated_id"):
            lines.append("已生成新的活动 ID。")
        if iso_date:
            lines.append(f"日期：{iso_date}。")
        if location:
            lines.append(f"地点：{location}。")
        if isinstance(host, dict) and host:
            host_label = host.get("name") or host.get("id") or host.get("email")
            if host_label:
                lines.append(f"主持人：{host_label}。")

        return {"message": "\n".join(lines)}


class FormatRsvpReplyNode(TaskNode):
    """Format reply after an RSVP update."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Compose the RSVP update confirmation message."""
        results = state.get("results", {})
        payload = results.get("prepare_rsvp", {})

        attendee = (
            payload.get("attendee_name") or payload.get("attendee_id") or "参与者"
        )
        status = payload.get("status") or "已更新"
        event_id = payload.get("event_id") or ""

        message = f"RSVP 已记录：{attendee} 状态为 {status}"
        if event_id:
            message = f"{message}（活动 {event_id}）。"
        else:
            message = f"{message}。"
        return {"message": message}


class FormatRsvpListReplyNode(TaskNode):
    """Format RSVP list reply."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Summarize the RSVP list response."""
        results = state.get("results", {})
        payload = results.get("prepare_get_rsvps", {})
        event_id = payload.get("event_id") or ""
        find_result = results.get("find_rsvps", {})
        data = find_result.get("data") if isinstance(find_result, dict) else None

        if not isinstance(data, list) or not data:
            if event_id:
                return {"message": f"活动 {event_id} 暂无 RSVP。"}
            return {"message": "未找到 RSVP。"}

        lines = []
        if event_id:
            lines.append(f"活动 {event_id} 的 RSVP：")
        else:
            lines.append("RSVP 列表：")

        for item in data:
            attendee_name = item.get("attendee_name") or ""
            attendee_id = item.get("attendee_id") or ""
            status = item.get("status") or "unknown"
            if attendee_name and attendee_id and attendee_name != attendee_id:
                label = f"{attendee_name} ({attendee_id})"
            else:
                label = attendee_name or attendee_id or "未知参与者"
            lines.append(f"- {label}：{status}")

        return {"message": "\n".join(lines)}


class FormatEventListReplyNode(TaskNode):
    """Format event list reply."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Compile the list of upcoming events for the reply."""
        results = state.get("results", {})
        find_result = (
            results.get("find_events", {}) if isinstance(results, dict) else {}
        )
        data = find_result.get("data") if isinstance(find_result, dict) else None

        if not isinstance(data, list) or not data:
            return {"message": "未找到活动。"}

        lines = ["近期活动："]
        for item in data:
            title = item.get("title") or "未命名活动"
            event_id = item.get("event_id") or ""
            iso_date = item.get("iso_date") or ""
            location = item.get("location") or ""
            details = [title]
            if iso_date:
                details.append(f"{iso_date}")
            if location:
                details.append(location)
            label = " | ".join(details)
            if event_id:
                label = f"{label}（ID：{event_id}）"
            lines.append(f"- {label}")

        return {"message": "\n".join(lines)}


class FormatEventDetailReplyNode(TaskNode):
    """Format reply for event detail query."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Format the event details for the reply."""
        results = state.get("results", {})
        payload = results.get("prepare_get_event", {})
        event_id = payload.get("event_id") or ""
        find_result = results.get("find_event_detail", {})
        data = find_result.get("data") if isinstance(find_result, dict) else None

        if not isinstance(data, list) or not data:
            if event_id:
                return {"message": f"未找到活动（ID：{event_id}）。"}
            return {"message": "未找到活动。"}

        event = data[0]
        title = event.get("title") or "未命名活动"
        description = event.get("description") or ""
        iso_date = event.get("iso_date") or ""
        location = event.get("location") or ""
        host = event.get("host") or {}
        status = event.get("status") or "active"
        creator_id = event.get("creator_id") or ""

        lines = [f"活动详情（ID：{event_id}）："]
        lines.append(f"标题：{title}")
        if description:
            lines.append(f"描述：{description}")
        if iso_date:
            lines.append(f"日期：{iso_date}")
        if location:
            lines.append(f"地点：{location}")
        if isinstance(host, dict) and host:
            host_label = host.get("name") or host.get("id") or host.get("email")
            if host_label:
                lines.append(f"主持人：{host_label}")
        lines.append(f"状态：{status}")
        if creator_id:
            lines.append(f"创建者：{creator_id}")

        return {"message": "\n".join(lines)}


class FormatUnknownReplyNode(TaskNode):
    """Format reply for unknown commands."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Provide guidance when the command is not recognized."""
        results = state.get("results", {})
        fallback_result = results.get("extract_fallback_reply", {})
        fallback_message = ""
        if isinstance(fallback_result, Mapping):
            fallback_message = str(fallback_result.get("agent_reply", "")).strip()
        if fallback_message:
            return {"message": f"{fallback_message}\n\n如需重置对话，请发送 /重置聊天"}
        return {
            "message": (
                "我可以更新活动、记录 RSVP，查询活动详情或查询 RSVP。"
                "示例：'更新活动 ...'、'为活动 <id> RSVP yes'、"
                "'查询活动 <id> 的详情'或 '查询活动 <id> 的 RSVP'。"
                "\n\n如需重置对话，请发送 /重置聊天"
            )
        }


async def build_graph() -> StateGraph:
    """Build the WeCom event agent workflow."""
    graph = StateGraph(State)
    register_wecom_and_agent_nodes(graph)
    register_parser_nodes(graph)
    register_prepare_nodes(graph)
    register_mongodb_nodes(graph)
    register_format_nodes(graph)
    register_routing_nodes(graph)
    register_send_nodes(graph)
    setup_initial_routing(graph)
    setup_action_routing(graph)
    setup_event_flow(graph)
    setup_rsvp_flow(graph)
    setup_get_and_list_flow(graph)
    setup_unknown_flow(graph)
    setup_reply_channel(graph)
    return graph


def register_wecom_and_agent_nodes(graph: StateGraph) -> None:
    """Register the nodes that set up WeCom credentials and agent behavior."""
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
            app_secret="[[wecom_app_secret_orcheo]]",
        ),
    )

    graph.add_node(
        "get_cs_access_token",
        WeComAccessTokenNode(
            name="get_cs_access_token",
            app_secret="[[wecom_app_secret_orcheo]]",
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
            system_prompt=(
                "你是活动运营助手。请仅输出 JSON（不要 markdown），将用户消息解析为"
                "以下 schema："
                '{"action": "update_event|end_event|get_event|update_rsvp|get_rsvps|'
                'list_events|unknown", '
                '"event": {"event_id": "", "title": "", '
                '"description": "", "iso_date": "", '
                '"status": "active|ended", '
                '"location": "", "host": {"name": "", "id": "", "email": ""}}, '
                '"rsvp": {"event_id": "", "attendee_id": "", '
                '"attendee_name": "", "status": '
                '"yes|no|maybe|cancelled"}}。'
                "日期使用 ISO 8601 格式。未知字段留空。"
            ),
            reset_command="/重置聊天",
        ),
    )
    graph.add_node(
        "fallback_agent",
        AgentNode(
            name="fallback_agent",
            ai_model=DEFAULT_MODEL,
            model_kwargs={"api_key": "[[openai_api_key]]"},
            system_prompt=(
                "你是活动运营助手。用户的请求无法处理或聊天被重置。请用用户最新消息的语言"
                "回复简短、有帮助的指引，说明你可以做的事情：更新活动、查询活动详情、"
                "记录 RSVP、查询 RSVP 或列出活动。如有缺失信息，请提示补充。"
                "不要返回 JSON 或 markdown。"
            ),
            reset_command="/重置聊天",
        ),
    )


def register_parser_nodes(graph: StateGraph) -> None:
    """Register parser nodes that extract agent replies and parse commands."""
    graph.add_node(
        "extract_agent_reply",
        ExtractAgentReplyNode(name="extract_agent_reply"),
    )
    graph.add_node(
        "extract_fallback_reply",
        ExtractAgentReplyNode(name="extract_fallback_reply"),
    )
    graph.add_node("parse_command", ParseCommandNode(name="parse_command"))


def register_prepare_nodes(graph: StateGraph) -> None:
    """Register preparation nodes for handling event, RSVP, and list actions."""
    graph.add_node("prepare_event", PrepareEventUpdateNode(name="prepare_event"))
    graph.add_node("prepare_rsvp", PrepareRsvpUpdateNode(name="prepare_rsvp"))
    graph.add_node("prepare_get_rsvps", PrepareGetRsvpsNode(name="prepare_get_rsvps"))
    graph.add_node("prepare_get_event", PrepareGetEventNode(name="prepare_get_event"))
    graph.add_node(
        "prepare_list_events", PrepareListEventsNode(name="prepare_list_events")
    )
    graph.add_node(
        "validate_event_owner", ValidateEventOwnerNode(name="validate_event_owner")
    )


def register_mongodb_nodes(graph: StateGraph) -> None:
    """Register MongoDB nodes for searching and updating events and RSVPs."""
    graph.add_node(
        "find_event_for_update",
        MongoDBFindNode(
            name="find_event_for_update",
            database="{{config.configurable.events_database}}",
            collection="{{config.configurable.events_collection}}",
            filter={"event_id": "{{prepare_event.event_id}}"},
            limit=1,
        ),
    )
    graph.add_node(
        "upsert_event",
        MongoDBNode(
            name="upsert_event",
            operation="update_one",
            database="{{config.configurable.events_database}}",
            collection="{{config.configurable.events_collection}}",
            filter="{{prepare_event.filter}}",
            update="{{prepare_event.update}}",
            options={"upsert": True},
        ),
    )
    graph.add_node(
        "upsert_rsvp",
        MongoDBNode(
            name="upsert_rsvp",
            operation="update_one",
            database="{{config.configurable.events_database}}",
            collection="{{config.configurable.rsvps_collection}}",
            filter="{{prepare_rsvp.filter}}",
            update="{{prepare_rsvp.update}}",
            options={"upsert": True},
        ),
    )
    graph.add_node(
        "find_rsvps",
        MongoDBFindNode(
            name="find_rsvps",
            database="{{config.configurable.events_database}}",
            collection="{{config.configurable.rsvps_collection}}",
            filter={"event_id": "{{prepare_get_rsvps.event_id}}"},
            sort={"updated_at": -1},
            limit=200,
        ),
    )
    graph.add_node(
        "find_events",
        MongoDBFindNode(
            name="find_events",
            database="{{config.configurable.events_database}}",
            collection="{{config.configurable.events_collection}}",
            filter={
                "iso_date": {"$gte": "{{prepare_list_events.now}}"},
                "status": {"$ne": "ended"},
                "ended_at": {"$exists": False},
            },
            sort={"iso_date": 1},
            limit="{{prepare_list_events.limit}}",
        ),
    )
    graph.add_node(
        "find_event_detail",
        MongoDBFindNode(
            name="find_event_detail",
            database="{{config.configurable.events_database}}",
            collection="{{config.configurable.events_collection}}",
            filter={"event_id": "{{prepare_get_event.event_id}}"},
            limit=1,
        ),
    )


def register_format_nodes(graph: StateGraph) -> None:
    """Register formatting nodes that build user-facing replies."""
    graph.add_node(
        "format_event_reply", FormatEventReplyNode(name="format_event_reply")
    )
    graph.add_node(
        "format_event_error", FormatEventErrorNode(name="format_event_error")
    )
    graph.add_node("format_rsvp_reply", FormatRsvpReplyNode(name="format_rsvp_reply"))
    graph.add_node(
        "format_rsvp_list_reply",
        FormatRsvpListReplyNode(name="format_rsvp_list_reply"),
    )
    graph.add_node(
        "format_event_list_reply",
        FormatEventListReplyNode(name="format_event_list_reply"),
    )
    graph.add_node(
        "format_event_detail_reply",
        FormatEventDetailReplyNode(name="format_event_detail_reply"),
    )
    graph.add_node(
        "format_unknown_reply",
        FormatUnknownReplyNode(name="format_unknown_reply"),
    )


def register_routing_nodes(graph: StateGraph) -> None:
    """Register routing nodes keyed by the intent and reply types."""
    graph.add_node("route_by_type", NoOpNode(name="route_by_type"))
    graph.add_node("route_action_rsvp", NoOpNode(name="route_action_rsvp"))
    graph.add_node("route_action_get", NoOpNode(name="route_action_get"))
    graph.add_node("route_action_get_event", NoOpNode(name="route_action_get_event"))
    graph.add_node("route_action_list", NoOpNode(name="route_action_list"))
    graph.add_node("route_event_reply", NoOpNode(name="route_event_reply"))
    graph.add_node("route_event_error", NoOpNode(name="route_event_error"))
    graph.add_node("route_rsvp_reply", NoOpNode(name="route_rsvp_reply"))
    graph.add_node("route_rsvp_error", NoOpNode(name="route_rsvp_error"))
    graph.add_node("route_rsvp_list_reply", NoOpNode(name="route_rsvp_list_reply"))
    graph.add_node("route_event_list_reply", NoOpNode(name="route_event_list_reply"))
    graph.add_node(
        "route_event_detail_reply", NoOpNode(name="route_event_detail_reply")
    )
    graph.add_node("route_get_event_error", NoOpNode(name="route_get_event_error"))
    graph.add_node("route_get_error", NoOpNode(name="route_get_error"))
    graph.add_node("route_unknown_reply", NoOpNode(name="route_unknown_reply"))


def register_send_nodes(graph: StateGraph) -> None:
    """Register nodes responsible for sending replies through WeCom."""
    graph.add_node(
        "send_event_reply",
        WeComCustomerServiceSendNode(
            name="send_event_reply",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{format_event_reply.message}}",
        ),
    )
    graph.add_node(
        "send_event_reply_internal",
        WeComSendMessageNode(
            name="send_event_reply_internal",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{format_event_reply.message}}",
        ),
    )
    graph.add_node(
        "send_event_error",
        WeComCustomerServiceSendNode(
            name="send_event_error",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{format_event_error.message}}",
        ),
    )
    graph.add_node(
        "send_event_error_internal",
        WeComSendMessageNode(
            name="send_event_error_internal",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{format_event_error.message}}",
        ),
    )
    graph.add_node(
        "send_rsvp_reply",
        WeComCustomerServiceSendNode(
            name="send_rsvp_reply",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{format_rsvp_reply.message}}",
        ),
    )
    graph.add_node(
        "send_rsvp_reply_internal",
        WeComSendMessageNode(
            name="send_rsvp_reply_internal",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{format_rsvp_reply.message}}",
        ),
    )
    graph.add_node(
        "send_rsvp_error",
        WeComCustomerServiceSendNode(
            name="send_rsvp_error",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{prepare_rsvp.reply_message}}",
        ),
    )
    graph.add_node(
        "send_rsvp_error_internal",
        WeComSendMessageNode(
            name="send_rsvp_error_internal",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{prepare_rsvp.reply_message}}",
        ),
    )
    graph.add_node(
        "send_rsvp_list_reply",
        WeComCustomerServiceSendNode(
            name="send_rsvp_list_reply",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{format_rsvp_list_reply.message}}",
        ),
    )
    graph.add_node(
        "send_rsvp_list_reply_internal",
        WeComSendMessageNode(
            name="send_rsvp_list_reply_internal",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{format_rsvp_list_reply.message}}",
        ),
    )
    graph.add_node(
        "send_event_list_reply",
        WeComCustomerServiceSendNode(
            name="send_event_list_reply",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{format_event_list_reply.message}}",
        ),
    )
    graph.add_node(
        "send_event_list_reply_internal",
        WeComSendMessageNode(
            name="send_event_list_reply_internal",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{format_event_list_reply.message}}",
        ),
    )
    graph.add_node(
        "send_event_detail_reply",
        WeComCustomerServiceSendNode(
            name="send_event_detail_reply",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{format_event_detail_reply.message}}",
        ),
    )
    graph.add_node(
        "send_event_detail_reply_internal",
        WeComSendMessageNode(
            name="send_event_detail_reply_internal",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{format_event_detail_reply.message}}",
        ),
    )
    graph.add_node(
        "send_get_event_error",
        WeComCustomerServiceSendNode(
            name="send_get_event_error",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{prepare_get_event.reply_message}}",
        ),
    )
    graph.add_node(
        "send_get_event_error_internal",
        WeComSendMessageNode(
            name="send_get_event_error_internal",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{prepare_get_event.reply_message}}",
        ),
    )
    graph.add_node(
        "send_get_error",
        WeComCustomerServiceSendNode(
            name="send_get_error",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{prepare_get_rsvps.reply_message}}",
        ),
    )
    graph.add_node(
        "send_get_error_internal",
        WeComSendMessageNode(
            name="send_get_error_internal",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{prepare_get_rsvps.reply_message}}",
        ),
    )
    graph.add_node(
        "send_unknown_reply",
        WeComCustomerServiceSendNode(
            name="send_unknown_reply",
            open_kf_id="{{wecom_cs_sync.open_kf_id}}",
            external_userid="{{wecom_cs_sync.external_userid}}",
            message="{{format_unknown_reply.message}}",
        ),
    )
    graph.add_node(
        "send_unknown_reply_internal",
        WeComSendMessageNode(
            name="send_unknown_reply_internal",
            agent_id="{{config.configurable.agent_id}}",
            to_user="{{wecom_events_parser.target_user}}",
            message="{{format_unknown_reply.message}}",
        ),
    )


def setup_initial_routing(graph: StateGraph) -> None:
    """Configure the entry-point and handling for incoming messages."""
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
    graph.add_conditional_edges(
        "route_by_type",
        message_type_router,
        {
            "true": "get_cs_access_token",
            "false": "get_access_token",
        },
    )
    graph.add_edge("get_cs_access_token", "wecom_cs_sync")
    graph.add_edge("get_access_token", "agent")

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
            "false": "agent",
        },
    )


def setup_action_routing(graph: StateGraph) -> None:
    """Route parsed commands to the appropriate preparation nodes."""
    graph.add_edge("agent", "extract_agent_reply")
    graph.add_edge("extract_agent_reply", "parse_command")

    action_event_router = IfElseEdge(
        name="action_event_router",
        conditions=[
            Condition(
                left="{{parse_command.action}}",
                operator="equals",
                right="update_event",
            ),
            Condition(
                left="{{parse_command.action}}",
                operator="equals",
                right="end_event",
            ),
        ],
        condition_logic="or",
    )
    graph.add_conditional_edges(
        "parse_command",
        action_event_router,
        {
            "true": "prepare_event",
            "false": "route_action_rsvp",
        },
    )

    action_rsvp_router = IfElseEdge(
        name="action_rsvp_router",
        conditions=[
            Condition(
                left="{{parse_command.action}}",
                operator="equals",
                right="update_rsvp",
            )
        ],
    )
    graph.add_conditional_edges(
        "route_action_rsvp",
        action_rsvp_router,
        {
            "true": "prepare_rsvp",
            "false": "route_action_get",
        },
    )

    action_get_router = IfElseEdge(
        name="action_get_router",
        conditions=[
            Condition(
                left="{{parse_command.action}}",
                operator="equals",
                right="get_rsvps",
            )
        ],
    )
    graph.add_conditional_edges(
        "route_action_get",
        action_get_router,
        {
            "true": "prepare_get_rsvps",
            "false": "route_action_get_event",
        },
    )

    action_get_event_router = IfElseEdge(
        name="action_get_event_router",
        conditions=[
            Condition(
                left="{{parse_command.action}}",
                operator="equals",
                right="get_event",
            )
        ],
    )
    graph.add_conditional_edges(
        "route_action_get_event",
        action_get_event_router,
        {
            "true": "prepare_get_event",
            "false": "route_action_list",
        },
    )

    action_list_router = IfElseEdge(
        name="action_list_router",
        conditions=[
            Condition(
                left="{{parse_command.action}}",
                operator="equals",
                right="list_events",
            )
        ],
    )
    graph.add_conditional_edges(
        "route_action_list",
        action_list_router,
        {
            "true": "prepare_list_events",
            "false": "fallback_agent",
        },
    )


def setup_event_flow(graph: StateGraph) -> None:
    """Wire the validation and persistence flow for event updates."""
    event_valid_router = IfElseEdge(
        name="event_valid_router",
        conditions=[
            Condition(
                left="{{prepare_event.is_valid}}",
                operator="is_truthy",
            )
        ],
    )
    graph.add_conditional_edges(
        "prepare_event",
        event_valid_router,
        {
            "true": "find_event_for_update",
            "false": "format_event_error",
        },
    )
    graph.add_edge("find_event_for_update", "validate_event_owner")

    event_owner_router = IfElseEdge(
        name="event_owner_router",
        conditions=[
            Condition(
                left="{{validate_event_owner.is_valid}}",
                operator="is_truthy",
            )
        ],
    )
    graph.add_conditional_edges(
        "validate_event_owner",
        event_owner_router,
        {
            "true": "upsert_event",
            "false": "format_event_error",
        },
    )
    graph.add_edge("upsert_event", "format_event_reply")
    graph.add_edge("format_event_reply", "route_event_reply")
    graph.add_edge("format_event_error", "route_event_error")


def setup_rsvp_flow(graph: StateGraph) -> None:
    """Wire the RSVP validation and persistence flow."""
    rsvp_valid_router = IfElseEdge(
        name="rsvp_valid_router",
        conditions=[
            Condition(
                left="{{prepare_rsvp.is_valid}}",
                operator="is_truthy",
            )
        ],
    )
    graph.add_conditional_edges(
        "prepare_rsvp",
        rsvp_valid_router,
        {
            "true": "upsert_rsvp",
            "false": "route_rsvp_error",
        },
    )
    graph.add_edge("upsert_rsvp", "format_rsvp_reply")
    graph.add_edge("format_rsvp_reply", "route_rsvp_reply")


def setup_get_and_list_flow(graph: StateGraph) -> None:
    """Arrange the flows for fetching RSVP lists, event details, and upcoming events."""
    get_valid_router = IfElseEdge(
        name="get_valid_router",
        conditions=[
            Condition(
                left="{{prepare_get_rsvps.is_valid}}",
                operator="is_truthy",
            )
        ],
    )
    graph.add_conditional_edges(
        "prepare_get_rsvps",
        get_valid_router,
        {
            "true": "find_rsvps",
            "false": "route_get_error",
        },
    )
    graph.add_edge("find_rsvps", "format_rsvp_list_reply")
    graph.add_edge("format_rsvp_list_reply", "route_rsvp_list_reply")

    get_event_valid_router = IfElseEdge(
        name="get_event_valid_router",
        conditions=[
            Condition(
                left="{{prepare_get_event.is_valid}}",
                operator="is_truthy",
            )
        ],
    )
    graph.add_conditional_edges(
        "prepare_get_event",
        get_event_valid_router,
        {
            "true": "find_event_detail",
            "false": "route_get_event_error",
        },
    )
    graph.add_edge("find_event_detail", "format_event_detail_reply")
    graph.add_edge("format_event_detail_reply", "route_event_detail_reply")

    graph.add_edge("prepare_list_events", "find_events")
    graph.add_edge("find_events", "format_event_list_reply")
    graph.add_edge("format_event_list_reply", "route_event_list_reply")


def setup_unknown_flow(graph: StateGraph) -> None:
    """Handle fallback requests that the agent cannot interpret."""
    graph.add_edge("fallback_agent", "extract_fallback_reply")
    graph.add_edge("extract_fallback_reply", "format_unknown_reply")
    graph.add_edge("format_unknown_reply", "route_unknown_reply")


def setup_reply_channel(graph: StateGraph) -> None:
    """Route finalized replies either to customer service or internally."""
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
        "route_event_reply",
        reply_channel_router,
        {
            "true": "send_event_reply",
            "false": "send_event_reply_internal",
        },
    )
    graph.add_conditional_edges(
        "route_event_error",
        reply_channel_router,
        {
            "true": "send_event_error",
            "false": "send_event_error_internal",
        },
    )
    graph.add_conditional_edges(
        "route_rsvp_reply",
        reply_channel_router,
        {
            "true": "send_rsvp_reply",
            "false": "send_rsvp_reply_internal",
        },
    )
    graph.add_conditional_edges(
        "route_rsvp_error",
        reply_channel_router,
        {
            "true": "send_rsvp_error",
            "false": "send_rsvp_error_internal",
        },
    )
    graph.add_conditional_edges(
        "route_rsvp_list_reply",
        reply_channel_router,
        {
            "true": "send_rsvp_list_reply",
            "false": "send_rsvp_list_reply_internal",
        },
    )
    graph.add_conditional_edges(
        "route_event_list_reply",
        reply_channel_router,
        {
            "true": "send_event_list_reply",
            "false": "send_event_list_reply_internal",
        },
    )
    graph.add_conditional_edges(
        "route_event_detail_reply",
        reply_channel_router,
        {
            "true": "send_event_detail_reply",
            "false": "send_event_detail_reply_internal",
        },
    )
    graph.add_conditional_edges(
        "route_get_event_error",
        reply_channel_router,
        {
            "true": "send_get_event_error",
            "false": "send_get_event_error_internal",
        },
    )
    graph.add_conditional_edges(
        "route_get_error",
        reply_channel_router,
        {
            "true": "send_get_error",
            "false": "send_get_error_internal",
        },
    )
    graph.add_conditional_edges(
        "route_unknown_reply",
        reply_channel_router,
        {
            "true": "send_unknown_reply",
            "false": "send_unknown_reply_internal",
        },
    )
    graph.add_edge("send_event_reply", END)
    graph.add_edge("send_event_reply_internal", END)
    graph.add_edge("send_event_error", END)
    graph.add_edge("send_event_error_internal", END)
    graph.add_edge("send_rsvp_reply", END)
    graph.add_edge("send_rsvp_reply_internal", END)
    graph.add_edge("send_rsvp_error", END)
    graph.add_edge("send_rsvp_error_internal", END)
    graph.add_edge("send_rsvp_list_reply", END)
    graph.add_edge("send_rsvp_list_reply_internal", END)
    graph.add_edge("send_event_list_reply", END)
    graph.add_edge("send_event_list_reply_internal", END)
    graph.add_edge("send_event_detail_reply", END)
    graph.add_edge("send_event_detail_reply_internal", END)
    graph.add_edge("send_get_event_error", END)
    graph.add_edge("send_get_event_error_internal", END)
    graph.add_edge("send_get_error", END)
    graph.add_edge("send_get_error_internal", END)
    graph.add_edge("send_unknown_reply", END)
    graph.add_edge("send_unknown_reply_internal", END)
