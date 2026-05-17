"""Runtime graph builder tests."""

from __future__ import annotations
import pytest
from orcheo.graph.builder import (
    UnsupportedWorkflowGraphFormatError,
    build_graph,
)


SCRIPT = """
from langgraph.graph import END, START, StateGraph

def build_graph():
    graph = StateGraph(dict)

    def code(state):
        return {"message": "hello"}

    graph.add_node("code", code)
    graph.add_edge(START, "code")
    graph.add_edge("code", END)
    return graph
""".strip()


def test_build_graph_from_langgraph_script() -> None:
    compiled = build_graph(
        {
            "format": "langgraph-script",
            "source": SCRIPT,
            "entrypoint": "build_graph",
        }
    ).compile()
    mermaid = compiled.get_graph().draw_mermaid()
    assert "code" in mermaid


def test_build_graph_rejects_non_script_runtime_payload() -> None:
    with pytest.raises(UnsupportedWorkflowGraphFormatError, match="unknown"):
        build_graph({"nodes": [], "edges": []})
