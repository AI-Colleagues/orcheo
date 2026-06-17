from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from orcheo.graph.ingestion.summary import _as_state_graph, summarise_graph_index
from orcheo.graph.state import State


def test_as_state_graph_handles_stategraph_and_compiled_builder() -> None:
    graph = StateGraph(State)
    graph.add_node("noop", lambda state: state)
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)

    assert _as_state_graph(graph) is graph

    compiled = graph.compile()
    assert _as_state_graph(compiled) is graph


def test_summarise_graph_index_uses_rendered_mermaid_when_available() -> None:
    graph = StateGraph(State)
    graph.add_node("noop", lambda state: state)
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)

    index = summarise_graph_index(graph)

    assert isinstance(index.get("mermaid"), str)
    assert "graph TD" in index["mermaid"]
