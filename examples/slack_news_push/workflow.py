"""Slack news digest workflow with mention and cron triggers.

Configure Slack Events API to send requests to:
`/api/workflows/{workflow_id}/triggers/webhook?preserve_raw_body=true`
so signatures can be verified.

Configurable inputs:
- channel_id (single channel ID)
- database (MongoDB database)
- collection (MongoDB collection)
- item_limit (MongoDB item limit)
- team_id (Slack workspace ID)
"""

import html
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from orcheo.edges import Condition, IfElseEdge
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.connectors.slack import SlackEventsParserNode, SlackNode
from orcheo.nodes.integrations.databases.mongodb import (
    MongoDBAggregateNode,
    MongoDBFindNode,
    MongoDBUpdateManyNode,
)
from orcheo.nodes.triggers import CronTriggerNode


class DetectTriggerNode(TaskNode):
    """Detect whether the workflow was invoked by a webhook payload."""

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return whether a webhook body is present in inputs."""
        inputs = state.get("inputs", {})
        has_webhook = bool(inputs.get("body"))
        return {"has_webhook": has_webhook}


class FormatDigestNode(TaskNode):
    """Format unread items into a Slack digest message."""

    @staticmethod
    def decode_title(text: str | None) -> str:
        """Decode and sanitize item titles from the news store."""
        if not text:
            return "No Title"
        decoded = html.unescape(text).replace("\xa0", " ")
        sanitized = decoded.replace("<", "[").replace(">", "]").replace("|", " - ")
        return " ".join(sanitized.split())

    @staticmethod
    def read_items(state: State) -> list[dict[str, Any]]:
        """Return the stored find_unread documents from the previous nodes."""
        results = state.get("results", {})
        if not isinstance(results, dict):
            return []
        find_result = results.get("find_unread", {})
        if not isinstance(find_result, dict):
            return []
        data = find_result.get("data")
        if isinstance(data, list):
            return data
        return []

    @staticmethod
    def read_unread_count(state: State) -> int:
        """Return the latest unread item count from the aggregation node."""
        results = state.get("results", {})
        if not isinstance(results, dict):
            return 0
        count_result = results.get("count_unread", {})
        if not isinstance(count_result, dict):
            return 0
        data = count_result.get("data")
        if not isinstance(data, list) or not data:
            return 0
        count = data[0].get("unread_count", 0)
        return int(count or 0)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return Slack-formatted digest text and item IDs."""
        items = self.read_items(state)
        total_unread = self.read_unread_count(state)

        lines = []
        for item in items:
            title = self.decode_title(item.get("title"))
            url = item.get("link", "")
            if isinstance(url, str) and url:
                lines.append(f"- <{url}|{title}>")
            else:
                lines.append(f"- {title}")

        remaining = max(total_unread - len(items), 0)
        body = "\n".join(lines)
        if body:
            body = f"{body}\n"

        news = f"{body}Unread count: {remaining}"
        return {
            "news": news,
            "ids": [item.get("_id") for item in items if item.get("_id") is not None],
        }


async def build_graph() -> StateGraph:
    """Build the simplified Slack mention responder workflow."""
    graph = StateGraph(State)
    graph.add_node("detect_trigger", DetectTriggerNode(name="detect_trigger"))
    graph.add_node(
        "cron_trigger",
        CronTriggerNode(
            name="cron_trigger",
            expression="0 9 * * *",
            timezone="Europe/Amsterdam",
        ),
    )
    graph.add_node(
        "slack_events_parser",
        SlackEventsParserNode(
            name="slack_events_parser",
            allowed_event_types=["app_mention"],
            channel_id="{{config.configurable.channel_id}}",
            timestamp_tolerance_seconds=300,
        ),
    )
    graph.add_node(
        "count_unread",
        MongoDBAggregateNode(
            name="count_unread",
            database="{{config.configurable.database}}",
            collection="{{config.configurable.collection}}",
            pipeline=[{"$match": {"read": False}}, {"$count": "unread_count"}],
        ),
    )
    graph.add_node(
        "find_unread",
        MongoDBFindNode(
            name="find_unread",
            database="{{config.configurable.database}}",
            collection="{{config.configurable.collection}}",
            filter={"read": False},
            sort={"isoDate": -1},
            limit="{{config.configurable.item_limit}}",
        ),
    )
    graph.add_node(
        "format_digest",
        FormatDigestNode(name="format_digest"),
    )
    graph.add_node(
        "post_message",
        SlackNode(
            name="post_message",
            tool_name="slack_post_message",
            team_id="{{config.configurable.team_id}}",
            kwargs={
                "channel_id": "{{config.configurable.channel_id}}",
                "text": "{{format_digest.news}}",
                "mrkdwn": True,
                "unfurl_links": False,
                "unfurl_media": False,
            },
        ),
    )
    graph.add_node(
        "mark_read",
        MongoDBUpdateManyNode(
            name="mark_read",
            database="{{config.configurable.database}}",
            collection="{{config.configurable.collection}}",
            filter={"_id": {"$in": "{{format_digest.ids}}"}},
            update={"$set": {"read": True}},
        ),
    )

    graph.set_entry_point("detect_trigger")
    trigger_router = IfElseEdge(
        name="trigger_router",
        conditions=[
            Condition(left="{{detect_trigger.has_webhook}}", operator="is_truthy")
        ],
    )
    graph.add_conditional_edges(
        "detect_trigger",
        trigger_router,
        {
            "true": "slack_events_parser",
            "false": "cron_trigger",
        },
    )
    graph.add_edge("cron_trigger", "count_unread")

    reply_router = IfElseEdge(
        name="reply_router",
        conditions=[
            Condition(
                left="{{slack_events_parser.should_process}}", operator="is_truthy"
            ),
        ],
    )
    graph.add_conditional_edges(
        "slack_events_parser",
        reply_router,
        {
            "true": "count_unread",
            "false": END,
        },
    )
    graph.add_edge("count_unread", "find_unread")
    graph.add_edge("find_unread", "format_digest")
    graph.add_edge("format_digest", "post_message")

    post_router = IfElseEdge(
        name="post_router",
        conditions=[
            Condition(left="{{post_message.is_error}}", operator="is_falsy"),
            Condition(left="{{format_digest.ids}}", operator="is_truthy"),
        ],
        condition_logic="and",
    )
    graph.add_conditional_edges(
        "post_message",
        post_router,
        {
            "true": "mark_read",
            "false": END,
        },
    )
    graph.add_edge("mark_read", END)
    return graph
