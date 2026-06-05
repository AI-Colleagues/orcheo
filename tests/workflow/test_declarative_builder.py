"""Tests for the declarative graph builder."""

from __future__ import annotations
import pytest
from orcheo.workflow.declarative_builder import (
    DeclarativeGraphBuildError,
    build_graph_from_declarative,
    is_declarative_graph_payload,
)


def _minimal_payload(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {
        "format": "orcheo-declarative-graph",
        "version": 1,
        "nodes": nodes,
        "edges": edges or [],
        "conditional_edges": [],
        "triggers": [],
        "listeners": [],
        "credential_references": [],
        "metadata": {},
    }


def test_is_declarative_graph_payload_true() -> None:
    assert is_declarative_graph_payload({"format": "orcheo-declarative-graph"})


def test_is_declarative_graph_payload_false() -> None:
    assert not is_declarative_graph_payload({"format": "langgraph-script"})
    assert not is_declarative_graph_payload({})


def test_wrong_format_raises() -> None:
    payload = _minimal_payload([])
    payload["format"] = "python-script"
    with pytest.raises(
        DeclarativeGraphBuildError, match="Expected declarative graph format"
    ):
        build_graph_from_declarative(payload)


def test_unknown_node_type_raises() -> None:
    payload = _minimal_payload(
        [{"id": "my_node", "type": "NonExistentNode9999", "config": {}}]
    )
    with pytest.raises(DeclarativeGraphBuildError, match="Unknown node type"):
        build_graph_from_declarative(payload)


def test_empty_graph_builds_successfully() -> None:
    payload = _minimal_payload([], [])
    graph = build_graph_from_declarative(payload)
    assert graph is not None


def test_known_node_type_builds_successfully() -> None:
    # ManualTriggerNode is a simple node in the registry; import it so it registers
    import orcheo.nodes.triggers  # noqa: F401

    payload = _minimal_payload(
        nodes=[{"id": "trigger", "type": "ManualTriggerNode", "config": {}}],
        edges=[
            {"source": "START", "target": "trigger"},
            {"source": "trigger", "target": "END"},
        ],
    )
    graph = build_graph_from_declarative(payload)
    assert graph is not None
