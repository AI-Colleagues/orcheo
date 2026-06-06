"""Tests for Mermaid diagram rendering from declarative workflow graphs."""

from __future__ import annotations
from orcheo.workflow.mermaid import (
    render_declarative_mermaid,
    render_mermaid_from_graph_payload,
)


def test_render_nodes_includes_name_and_type() -> None:
    summary = {
        "nodes": [{"name": "fetch", "type": "RSSNode"}],
        "edges": [],
        "conditional_edges": [],
    }
    mermaid = render_declarative_mermaid(summary)

    assert 'fetch["fetch\\n[RSSNode]"]' in mermaid


def test_render_nodes_name_only_when_no_type() -> None:
    summary = {
        "nodes": [{"name": "fetch"}],
        "edges": [],
        "conditional_edges": [],
    }
    mermaid = render_declarative_mermaid(summary)

    assert 'fetch["fetch"]' in mermaid


def test_render_nodes_uses_id_fallback() -> None:
    summary = {
        "nodes": [{"id": "my_node", "type": "AINode"}],
        "edges": [],
        "conditional_edges": [],
    }
    mermaid = render_declarative_mermaid(summary)

    assert "my_node" in mermaid


def test_render_nodes_deduplicates() -> None:
    summary = {
        "nodes": [{"name": "fetch", "type": "A"}, {"name": "fetch", "type": "B"}],
        "edges": [],
        "conditional_edges": [],
    }
    mermaid = render_declarative_mermaid(summary)

    node_lines = [l for l in mermaid.split("\n") if l.strip().startswith("fetch[")]
    assert len(node_lines) == 1


def test_render_nodes_skips_empty_name() -> None:
    summary = {
        "nodes": [{"type": "RSSNode"}],
        "edges": [],
        "conditional_edges": [],
    }
    mermaid = render_declarative_mermaid(summary)

    lines = mermaid.strip().split("\n")
    assert len(lines) == 1


def test_render_edges_tuple() -> None:
    summary = {
        "nodes": [],
        "edges": [("START", "fetch"), ("fetch", "END")],
        "conditional_edges": [],
    }
    mermaid = render_declarative_mermaid(summary)

    assert "START --> fetch" in mermaid
    assert "fetch --> END" in mermaid


def test_render_edges_dict() -> None:
    summary = {
        "nodes": [],
        "edges": [{"source": "START", "target": "fetch"}],
        "conditional_edges": [],
    }
    mermaid = render_declarative_mermaid(summary)

    assert "START --> fetch" in mermaid


def test_render_edges_skips_invalid() -> None:
    summary = {
        "nodes": [],
        "edges": [42, None, "bad"],
        "conditional_edges": [],
    }
    mermaid = render_declarative_mermaid(summary)

    lines = [l for l in mermaid.strip().split("\n") if "-->" in l]
    assert len(lines) == 0


def test_render_edges_skips_empty_src_or_tgt() -> None:
    summary = {
        "nodes": [],
        "edges": [{"source": "", "target": "fetch"}, {"source": "START", "target": ""}],
        "conditional_edges": [],
    }
    mermaid = render_declarative_mermaid(summary)

    lines = [l for l in mermaid.strip().split("\n") if "-->" in l]
    assert len(lines) == 0


def test_render_conditional_edges_with_mapping() -> None:
    summary = {
        "nodes": [],
        "edges": [],
        "conditional_edges": [
            {"source": "router", "mapping": {"ok": "send", "fail": "END"}}
        ],
    }
    mermaid = render_declarative_mermaid(summary)

    assert "router -->|ok| send" in mermaid
    assert "router -->|fail| END" in mermaid


def test_render_conditional_edges_with_default() -> None:
    summary = {
        "nodes": [],
        "edges": [],
        "conditional_edges": [
            {"source": "router", "mapping": {}, "default": "fallback"}
        ],
    }
    mermaid = render_declarative_mermaid(summary)

    assert "router -->|default| fallback" in mermaid


def test_render_conditional_edges_skips_when_src_or_tgt_empty() -> None:
    summary = {
        "nodes": [],
        "edges": [],
        "conditional_edges": [
            {"source": "", "mapping": {"ok": "send"}, "default": "fallback"}
        ],
    }
    mermaid = render_declarative_mermaid(summary)

    lines = [l for l in mermaid.strip().split("\n") if "-->" in l]
    assert len(lines) == 0


def test_render_declarative_mermaid_starts_with_graph_td() -> None:
    mermaid = render_declarative_mermaid(
        {"nodes": [], "edges": [], "conditional_edges": []}
    )

    assert mermaid.startswith("graph TD")


def test_render_mermaid_from_graph_payload_returns_diagram() -> None:
    payload = {
        "format": "orcheo-declarative-graph",
        "summary": {
            "nodes": [{"name": "step", "type": "RSSNode"}],
            "edges": [("START", "step")],
            "conditional_edges": [],
        },
    }
    mermaid = render_mermaid_from_graph_payload(payload)

    assert mermaid is not None
    assert "step" in mermaid


def test_render_mermaid_from_graph_payload_returns_none_for_wrong_format() -> None:
    payload = {"format": "langgraph-script", "summary": {}}

    assert render_mermaid_from_graph_payload(payload) is None


def test_render_mermaid_from_graph_payload_returns_none_for_missing_summary() -> None:
    payload = {"format": "orcheo-declarative-graph"}

    assert render_mermaid_from_graph_payload(payload) is None


def test_render_mermaid_from_graph_payload_returns_none_when_summary_not_dict() -> None:
    payload = {"format": "orcheo-declarative-graph", "summary": "bad"}

    assert render_mermaid_from_graph_payload(payload) is None
