"""Tests covering edge construction and conditional routing."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from typing import Any
import pytest
from langgraph.graph import END, START
from langgraph.types import Send
from orcheo.graph import END as ORCHEO_END
from orcheo.graph import START as ORCHEO_START
from orcheo.graph import StateGraph
from orcheo.graph.conditional import add_conditional_edges
from orcheo.graph.edges import build_edges
from orcheo.graph.state import State
from orcheo.nodes.logic import SetVariableNode
from tests.graph._builder_test_helpers import DummyGraph, StubDecision


def test_build_edge_nodes_missing_name() -> None:
    """Edge without name raises ValueError."""

    with pytest.raises(ValueError, match="Edge must have a name"):
        build_edges([{"type": "IfElseEdge"}])


def test_build_edge_nodes_unknown_type() -> None:
    """Unknown edge type raises ValueError."""

    with pytest.raises(ValueError, match="Unknown edge type: missing"):
        build_edges([{"name": "decision", "type": "missing"}])


def test_build_edge_nodes_success() -> None:
    """Successfully build edge instances."""
    from orcheo.edges.registry import edge_registry

    edges_config = [
        {
            "name": "my_decision",
            "type": "IfElseEdge",
            "conditions": [{"left": True, "operator": "is_truthy"}],
        }
    ]

    result = build_edges(edges_config)

    assert "my_decision" in result
    assert result["my_decision"].name == "my_decision"
    edge_class = edge_registry.get_edge("IfElseEdge")
    assert isinstance(result["my_decision"], edge_class)


def test_build_edge_nodes_rejects_legacy_alias_type() -> None:
    """Legacy edge aliases are rejected by the builder."""
    edges_config = [
        {
            "name": "my_decision",
            "type": "IfElse",
            "conditions": [{"left": True, "operator": "is_truthy"}],
        }
    ]

    with pytest.raises(ValueError, match="Unknown edge type: IfElse"):
        build_edges(edges_config)


@pytest.mark.parametrize(
    ("config", "expected_message"),
    [
        ({"path": "foo", "mapping": {"x": "END"}}, "source string"),
        ({"source": "A", "mapping": {"x": "END"}}, "path string"),
        ({"source": "A", "path": "foo"}, "non-empty mapping"),
    ],
)
def test_add_conditional_edges_validation(
    config: Mapping[str, Any], expected_message: str
) -> None:
    """Invalid conditional branch definitions raise detailed errors."""

    graph = DummyGraph()

    with pytest.raises(ValueError, match=expected_message):
        add_conditional_edges(graph, config, {})


def test_add_conditional_edges_maps_vertices() -> None:
    """Conditional edges normalise mapping keys and defaults."""

    graph = DummyGraph()

    add_conditional_edges(
        graph,
        {
            "source": "START",
            "path": "payload.flag",
            "mapping": {"true": "node_a", 0: "node_b"},
            "default": "END",
        },
        {},
    )

    assert graph.conditional_calls
    call = graph.conditional_calls[0]
    source, condition = call["args"][:2]
    assert source is START
    assert call["kwargs"] == {}
    assert condition({"payload": {"flag": True}}) == "node_a"
    assert condition({"payload": {"flag": 0}}) == "node_b"
    assert condition({"payload": {}}) is END


@pytest.mark.asyncio
async def test_state_graph_accepts_declarative_conditional_edge() -> None:
    """The Orcheo StateGraph export accepts restricted conditional configs."""
    graph = StateGraph(State)
    graph.add_node("flag", SetVariableNode(name="flag", variables={"go": True}))
    graph.add_node("target", SetVariableNode(name="target", variables={"hit": True}))
    graph.add_edge(ORCHEO_START, "flag")
    graph.add_conditional_edges(
        "flag",
        {
            "path": "results.flag.go",
            "mapping": {"true": "target", "false": ORCHEO_END},
        },
    )
    graph.add_edge("target", ORCHEO_END)

    compiled = graph.compile()
    result = await compiled.ainvoke({"inputs": {}})
    mermaid = compiled.get_graph().draw_mermaid()

    assert result["results"]["target"]["hit"] is True
    assert "flag -.-> target" in mermaid


def test_add_conditional_edges_without_default_raises() -> None:
    """When no default is provided, unmatched conditions raise a clear error."""

    graph = DummyGraph()

    add_conditional_edges(
        graph,
        {
            "source": "START",
            "path": "payload.flag",
            "mapping": {"true": "node_a"},
        },
        {},
    )

    call = graph.conditional_calls[0]
    condition = call["args"][1]
    with pytest.raises(ValueError, match="no 'default' target was configured"):
        condition({"payload": {"flag": "unknown"}})


def test_add_conditional_edges_preserves_default_for_edges() -> None:
    """Edge conditional edges apply default routing when unmatched."""

    graph = DummyGraph()
    edge = StubDecision(["true", "unknown"])

    add_conditional_edges(
        graph,
        {
            "source": "START",
            "path": "decision",
            "mapping": {"true": "END"},
            "default": "fallback",
        },
        {"decision": edge},
    )

    call = graph.conditional_calls[0]
    source, router = call["args"][:2]
    assert source is START
    assert asyncio.run(router({}, {})) is END
    assert asyncio.run(router({}, {})) == "fallback"


def test_add_conditional_edges_normalises_default_edges() -> None:
    """Edge defaults referencing sentinels are normalised before routing."""

    graph = DummyGraph()
    edge = StubDecision(["maybe"])

    add_conditional_edges(
        graph,
        {
            "source": "START",
            "path": "decision",
            "mapping": {"true": "END"},
            "default": "END",
        },
        {"decision": edge},
    )

    router = graph.conditional_calls[0]["args"][1]
    assert asyncio.run(router({}, {})) is END


def test_add_conditional_edges_edge_without_default_raises() -> None:
    """Edges without defaults raise a clear error when unmatched."""

    graph = DummyGraph()
    edge = StubDecision(["unknown"])

    add_conditional_edges(
        graph,
        {
            "source": "START",
            "path": "decision",
            "mapping": {"true": "next"},
        },
        {"decision": edge},
    )

    router = graph.conditional_calls[0]["args"][1]
    with pytest.raises(ValueError, match="no 'default' target was configured"):
        asyncio.run(router({}, {}))


def test_add_conditional_edges_edge_handles_sequence_results() -> None:
    """Edge routers normalise sequence outputs including Send packets."""

    graph = DummyGraph()
    send_packet = Send("custom", {})
    edge = StubDecision([["true", send_packet]])

    add_conditional_edges(
        graph,
        {
            "source": "START",
            "path": "decision",
            "mapping": {"true": "node_a"},
        },
        {"decision": edge},
    )

    router = graph.conditional_calls[0]["args"][1]
    destinations = asyncio.run(router({}, {}))
    assert destinations[0] == "node_a"
    assert destinations[1] is send_packet


def test_add_conditional_edges_without_edge() -> None:
    """Test conditional edges using state path (non-edge)."""

    graph = DummyGraph()

    add_conditional_edges(
        graph,
        {
            "source": "node_a",
            "path": "state.decision",
            "mapping": {"option1": "node_b", "option2": "node_c"},
        },
        {},
    )

    assert len(graph.conditional_calls) == 1
    call = graph.conditional_calls[0]
    source, condition = call["args"][:2]
    assert source == "node_a"
    assert callable(condition)
