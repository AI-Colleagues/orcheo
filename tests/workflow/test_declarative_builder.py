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


def test_node_config_name_field_not_overwritten() -> None:
    """When node config already has 'name', it should not be replaced by node_id."""
    import orcheo.nodes.triggers  # noqa: F401

    payload = _minimal_payload(
        nodes=[
            {
                "id": "trigger_node",
                "type": "ManualTriggerNode",
                "config": {"name": "custom_name"},
            }
        ]
    )
    graph = build_graph_from_declarative(payload)
    assert graph is not None


def test_conditional_edges_are_added() -> None:
    """Conditional edges with mapping and default should be wired into the graph."""
    import orcheo.nodes.triggers  # noqa: F401

    def _router(state: object) -> str:
        return "ok"

    payload = _minimal_payload(
        nodes=[
            {"id": "trigger", "type": "ManualTriggerNode", "config": {}},
            {"id": "step_a", "type": "ManualTriggerNode", "config": {}},
        ],
        edges=[{"source": "START", "target": "trigger"}],
    )
    payload["conditional_edges"] = [
        {
            "source": "trigger",
            "branch": _router,
            "mapping": {"ok": "step_a", "fail": "END"},
            "default": "END",
        }
    ]
    graph = build_graph_from_declarative(payload)
    assert graph is not None


def test_conditional_edges_without_default() -> None:
    """Conditional edges without a default value should still be added."""
    import orcheo.nodes.triggers  # noqa: F401

    def _router(state: object) -> str:
        return "ok"

    payload = _minimal_payload(
        nodes=[
            {"id": "trigger", "type": "ManualTriggerNode", "config": {}},
            {"id": "step_a", "type": "ManualTriggerNode", "config": {}},
        ],
        edges=[{"source": "START", "target": "trigger"}],
    )
    payload["conditional_edges"] = [
        {
            "source": "trigger",
            "branch": _router,
            "mapping": {"ok": "step_a"},
        }
    ]
    graph = build_graph_from_declarative(payload)
    assert graph is not None


def test_conditional_edges_empty_mapping_skipped() -> None:
    """Conditional edge with empty mapping and no default is skipped (line 69 false branch)."""
    payload = _minimal_payload(nodes=[], edges=[])
    payload["conditional_edges"] = [{"source": "trigger", "mapping": {}}]
    graph = build_graph_from_declarative(payload)
    assert graph is not None
