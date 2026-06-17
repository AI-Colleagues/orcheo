"""Tests for rendering workflow summaries as Mermaid diagrams."""

from __future__ import annotations
from orcheo.graph import mermaid


def test_normalise_mermaid_sentinels_replaces_public_labels() -> None:
    mermaid_text = "([<p>__start__</p>]) and (__end__) followed by ([<p>__end__</p>])"
    normalized = mermaid.normalise_mermaid_sentinels(mermaid_text)
    assert "START" in normalized
    assert "END" in normalized


def test_has_workflow_tool_subgraphs_traverses_nested_graphs() -> None:
    nested_summary = {"nodes": [{"name": "nested"}], "edges": []}
    summary = {
        "nodes": [
            {
                "name": "root",
                "workflow_tools": [
                    {"name": "broken_graph", "graph": "oops"},
                    {"name": "missing_summary", "graph": {"summary": "nope"}},
                    {"name": "nested_tool", "graph": {"summary": nested_summary}},
                ],
            }
        ],
    }
    assert mermaid.has_workflow_tool_subgraphs(summary)


def test_has_workflow_tool_subgraphs_detects_inlined_subgraph_node() -> None:
    nested_summary = {"nodes": [{"name": "nested"}], "edges": []}
    summary = {
        "nodes": [
            {"name": "broken_graph", "graph": "oops"},
            {"name": "missing_summary", "graph": {"summary": "nope"}},
            {"name": "branch", "graph": {"summary": nested_summary}},
        ],
    }
    assert mermaid.has_workflow_tool_subgraphs(summary)


def test_node_subgraph_summary_handles_invalid_and_valid_graphs() -> None:
    nested_summary = {"nodes": [{"name": "nested"}], "edges": []}
    assert mermaid._node_subgraph_summary({"graph": "oops"}) is None
    assert mermaid._node_subgraph_summary({"graph": {"summary": "nope"}}) is None
    assert mermaid._node_subgraph_summary({}) is None
    assert (
        mermaid._node_subgraph_summary({"graph": {"summary": nested_summary}})
        is nested_summary
    )


def test_render_summary_mermaid_expands_inlined_subgraph_node() -> None:
    nested = {
        "nodes": [{"name": "nested-node"}],
        "edges": [{"source": "START", "target": "nested-node"}],
    }
    summary = {
        "nodes": [{"name": "branch", "graph": {"summary": nested}}],
        "edges": [
            {"source": "START", "target": "branch"},
            {"source": "branch", "target": "END"},
        ],
    }
    diagram = mermaid.render_summary_mermaid(summary)
    assert 'subgraph root__branch__subgraph__subgraph["branch"]' in diagram
    assert "direction LR" in diagram
    assert "root__branch__subgraph__node__nested_node" in diagram
    # Inlined in place: no plain node box, no dotted line; edges hit the box.
    assert "root__node__branch[" not in diagram
    assert "-.->" not in diagram
    assert "root__start --> root__branch__subgraph__subgraph;" in diagram
    assert "root__branch__subgraph__subgraph --> root__end;" in diagram


def test_sequence_helpers_reject_strings_and_non_sequences() -> None:
    assert mermaid._sequence("string") == []
    assert mermaid._mapping_sequence("string") == []
    assert mermaid._sequence(123) == []


def test_mapping_sequence_filters_non_mappings() -> None:
    values = [{"keep": 1}, object(), {"also_keep": 2}]
    filtered = mermaid._mapping_sequence(values)
    assert filtered == [{"keep": 1}, {"also_keep": 2}]


def test_node_map_uses_id_label_and_name_fallbacks() -> None:
    summary = {
        "nodes": [
            {"id": "alpha"},
            {"label": "bravo"},
            {"name": "charlie"},
        ]
    }
    node_map = mermaid._node_map(summary)
    assert set(node_map) == {"alpha", "bravo", "charlie"}
    assert node_map["bravo"]["label"] == "bravo"


def test_node_map_skips_nodes_without_identifiers() -> None:
    summary = {
        "nodes": [
            {},
            {"name": None, "id": None, "label": None},
        ]
    }
    assert mermaid._node_map(summary) == {}


def test_collect_node_names_includes_edge_participants() -> None:
    summary = {
        "nodes": [{"name": "start"}],
        "edges": [
            {"source": "start", "target": "middle"},
            {"source": "middle", "target": "END"},
        ],
    }
    node_map = mermaid._node_map(summary)
    names = mermaid._collect_node_names(summary, node_map)
    assert "middle" in names
    assert "start" in names


def test_collect_edges_dedups_and_includes_branch_targets() -> None:
    summary = {
        "edges": [
            {"source": "alpha", "target": "beta"},
            {"from": "alpha", "to": "beta"},
            ["beta", "gamma"],
            ["beta"],
            {"source": "alpha", "target": None},
            {"source": "alpha", "target": ""},
        ],
        "conditional_edges": [
            {
                "source": "alpha",
                "mapping": {"case_a": "delta"},
                "default": "epsilon",
            }
        ],
    }
    edges = mermaid._collect_edges(summary)
    assert edges == [
        ("alpha", "beta"),
        ("beta", "gamma"),
        ("alpha", "delta"),
        ("alpha", "epsilon"),
    ]


def test_branch_targets_returns_mapping_values_then_default() -> None:
    branch = {"mapping": {"case": "delta"}, "default": "epsilon"}
    targets = mermaid._branch_targets(branch)
    assert targets == ["delta", "epsilon"]


def test_resolve_edge_handles_valid_and_invalid_inputs() -> None:
    assert mermaid._resolve_edge({"source": "alpha", "target": "beta"}) == (
        "alpha",
        "beta",
    )
    assert mermaid._resolve_edge({"from": "alpha", "to": "gamma"}) == (
        "alpha",
        "gamma",
    )
    assert mermaid._resolve_edge(["beta", "gamma"]) == ("beta", "gamma")
    assert mermaid._resolve_edge(["too", "many", "values"]) is None
    assert mermaid._resolve_edge("not an edge") is None
    assert mermaid._resolve_edge({"source": None, "target": "beta"}) is None
    assert mermaid._resolve_edge({"source": "alpha", "target": ""}) is None
    assert mermaid._resolve_edge({"source": "", "target": "beta"}) is None


def test_ensure_entry_edges_covers_all_branches() -> None:
    assert mermaid._ensure_entry_edges([], {"b", "a"}) == [("START", "a")]
    assert mermaid._ensure_entry_edges([], set()) == [("START", "END")]

    edges_with_start = [("START", "A")]
    assert mermaid._ensure_entry_edges(edges_with_start, {"A"}) == edges_with_start

    edges_missing_start = [("A", "B")]
    assert mermaid._ensure_entry_edges(edges_missing_start, {"A", "B", "C"}) == [
        ("A", "B"),
        ("START", "A"),
    ]

    edges_all_targeted = [("A", "B"), ("B", "A")]
    assert mermaid._ensure_entry_edges(edges_all_targeted, {"A", "B"}) == [
        ("A", "B"),
        ("B", "A"),
        ("START", "A"),
    ]


def test_vertex_node_and_sentinel_ids_use_start_and_end() -> None:
    start_id = mermaid._sentinel_id("prefix", "start")
    end_id = mermaid._sentinel_id("prefix", "end")
    assert mermaid._vertex_id("prefix", "START", start_id, end_id) == start_id
    assert mermaid._vertex_id("prefix", "END", start_id, end_id) == end_id
    assert mermaid._vertex_id("prefix", "node", start_id, end_id) == mermaid._node_id(
        "prefix", "node"
    )


def test_node_line_and_terminal_node_line_escape_labels() -> None:
    raw_label = 'Quote " and slash \\'
    formatted = mermaid._node_line("node-id", raw_label, "\t", node_class="tool")
    assert "\t" in formatted
    assert 'Quote \\" and slash \\\\' in formatted
    terminal = mermaid._terminal_node_line("term", raw_label, "last", "")
    assert '(["Quote \\" and slash \\\\"])' in terminal


def test_mermaid_id_sanitizes_numeric_prefixes() -> None:
    assert mermaid._mermaid_id("123!$") == "n_123__"


def test_render_summary_mermaid_creates_nested_subgraph() -> None:
    nested = {
        "nodes": [{"name": "nested-node"}],
        "edges": [{"source": "START", "target": "nested-node"}],
    }
    summary = {
        "nodes": [
            {
                "name": "main",
                "workflow_tools": [
                    {"name": "invalid_graph", "graph": "oops"},
                    {"name": "invalid_summary", "graph": {"summary": "nope"}},
                    {"name": 'Tool "X"', "graph": {"summary": nested}},
                ],
            },
            {"label": "secondary"},
        ],
        "edges": [{"source": "main", "target": "secondary"}],
        "conditional_edges": [
            {
                "source": "main",
                "mapping": {"case": "nested-node"},
                "default": "secondary",
            }
        ],
    }
    diagram = mermaid.render_summary_mermaid(summary)
    assert "graph TD;" in diagram
    assert "subgraph" in diagram
    assert 'Tool \\"X\\"' in diagram
    assert "-.->" in diagram
