"""Slack node example."""

import asyncio
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.connectors.slack import SlackNode


if __name__ == "__main__":
    load_dotenv()

    graph = StateGraph(State)
    slack_node = SlackNode(
        name="slack_node",
        tool_name="slack_post_message",
        kwargs={"channel_id": "C0946SY4TTM", "text": "Hello, world!"},
    )

    graph.add_node("slack_node", slack_node)
    graph.add_edge(START, "slack_node")
    graph.add_edge("slack_node", END)

    compiled_graph = graph.compile()
    result = asyncio.run(compiled_graph.ainvoke({}))
    print(result)
