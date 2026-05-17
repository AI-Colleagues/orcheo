"""Pull RSS updates from a list of RSS feeds."""

from __future__ import annotations
import asyncio
from langgraph.graph import END, START, StateGraph
from orcheo.graph.state import State
from orcheo.nodes.connectors.rss import RSSNode


if __name__ == "__main__":
    graph = StateGraph(State)

    rss_node = RSSNode(
        name="rss_node",
        sources=[
            "http://news.ycombinator.com/rss",
            "http://www.techradar.com/rss",
        ],
    )

    graph.add_node("rss_node", rss_node)
    graph.add_edge(START, "rss_node")
    graph.add_edge("rss_node", END)

    compiled_graph = graph.compile()
    result = asyncio.run(compiled_graph.ainvoke({"input": {}}))
    print(result)
