"""WeCom group news digest workflow on a daily schedule.

Configurable inputs (workflow_config.json):
- database (MongoDB database)
- collection (MongoDB collection)
- message_type (text or markdown)

Orcheo vault secrets required:
- wecom_group_webhook_key: WeCom group webhook key
"""

import html
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.integrations.databases.mongodb import MongoDBFindNode
from orcheo.nodes.triggers import CronTriggerNode
from orcheo.nodes.wecom import WeComGroupPushNode


class FormatDigestNode(TaskNode):
    """Format the latest news items into a WeCom digest message."""

    @staticmethod
    def decode_title(text: str | None) -> str:
        """Decode HTML entities and sanitize title text for display."""
        if not text:
            return "No Title"
        decoded = html.unescape(text).replace("\xa0", " ")
        return decoded.replace("<", "[").replace(">", "]")

    @staticmethod
    def read_items(state: State) -> list[dict[str, Any]]:
        """Extract news items from the find_latest node results."""
        results = state.get("results", {})
        if not isinstance(results, dict):
            return []
        find_result = results.get("find_latest", {})
        if not isinstance(find_result, dict):
            return []
        data = find_result.get("data")
        if isinstance(data, list):
            return data
        return []

    def format_line(self, title: str, url: str, msg_type: str) -> str:
        """Format a single news item as a line based on message type."""
        if msg_type == "markdown":
            if url:
                return f"- [{title}]({url})"
            return f"- {title}"
        if url:
            return f"- {title} {url}"
        return f"- {title}"

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Return the digest content string."""
        items = self.read_items(state)
        configurable = config.get("configurable", {})
        msg_type = configurable.get("message_type", "text")

        lines = []
        for item in items:
            title = self.decode_title(item.get("title"))
            url = item.get("link", "")
            lines.append(self.format_line(title, url, msg_type))

        content = "\n".join(lines) if lines else "No news items found."
        return {"content": content}


async def build_graph() -> StateGraph:
    """Build the WeCom news push workflow."""
    graph = StateGraph(State)

    graph.add_node(
        "cron_trigger",
        CronTriggerNode(
            name="cron_trigger",
            expression="0 9 * * *",
            timezone="Europe/Amsterdam",
        ),
    )
    graph.add_node(
        "find_latest",
        MongoDBFindNode(
            name="find_latest",
            database="{{config.configurable.database}}",
            collection="{{config.configurable.collection}}",
            sort={"isoDate": -1},
            limit=20,
        ),
    )
    graph.add_node(
        "format_digest",
        FormatDigestNode(name="format_digest"),
    )
    graph.add_node(
        "post_digest",
        WeComGroupPushNode(
            name="post_digest",
            msg_type="{{config.configurable.message_type}}",
            content="{{format_digest.content}}",
        ),
    )

    graph.set_entry_point("cron_trigger")
    graph.add_edge("cron_trigger", "find_latest")
    graph.add_edge("find_latest", "format_digest")
    graph.add_edge("format_digest", "post_digest")
    graph.add_edge("post_digest", END)

    return graph
