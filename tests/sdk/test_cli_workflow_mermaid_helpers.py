"""Workflow mermaid helper function tests."""

from __future__ import annotations


def test_workflow_mermaid_with_langgraph_summary() -> None:
    """Test mermaid generation when graph contains a LangGraph summary payload."""
    from orcheo_sdk.cli.workflow import _mermaid_from_graph

    graph = {
        "format": "langgraph-script",
        "source": "def build(): ...",
        "summary": {
            "nodes": [{"name": "store_secret", "type": "SetVariableNode"}],
            "edges": [["START", "store_secret"], ["store_secret", "END"]],
            "conditional_edges": [],
        },
    }

    mermaid = _mermaid_from_graph(graph)
    assert "store_secret" in mermaid
    assert "__start__ --> store_secret" in mermaid
    assert "store_secret --> __end__" in mermaid
    assert "[<p>START</p>]" in mermaid
    assert "[<p>END</p>]" in mermaid


def test_workflow_mermaid_prefers_precomputed_index_mermaid() -> None:
    """Use graph.index.mermaid when present."""
    from orcheo_sdk.cli.workflow import _mermaid_from_graph

    precomputed = "graph TD;\n\tA --> B;"
    graph = {
        "format": "langgraph-script",
        "index": {"mermaid": precomputed},
        "summary": {
            "nodes": [{"name": "ignored", "type": "SetVariableNode"}],
            "edges": [["START", "ignored"], ["ignored", "END"]],
            "conditional_edges": [],
        },
    }

    mermaid = _mermaid_from_graph(graph)
    assert mermaid == precomputed


def test_workflow_mermaid_ignores_blank_precomputed_index_mermaid(
    monkeypatch,
) -> None:
    """Blank precomputed Mermaid should fall through to the graph summary."""
    from orcheo_sdk.cli.workflow import mermaid as mermaid_module

    monkeypatch.setattr(
        mermaid_module,
        "_compiled_mermaid",
        lambda graph: "compiled-mermaid",
    )

    graph = {
        "format": "langgraph-script",
        "index": {"mermaid": "   "},
        "summary": {
            "nodes": [{"id": "node1"}],
            "edges": [],
            "conditional_edges": [],
        },
    }

    mermaid = mermaid_module._mermaid_from_graph(graph)
    assert mermaid == "compiled-mermaid"


def test_workflow_mermaid_with_langgraph_conditional_edges() -> None:
    """Conditional edges from LangGraph summary should be rendered."""
    from orcheo_sdk.cli.workflow import _mermaid_from_graph

    graph = {
        "format": "langgraph-script",
        "source": "def build(): ...",
        "summary": {
            "nodes": [
                {"name": "prepare_iteration", "type": "PrepareIterationNode"},
                {"name": "select_current_user", "type": "SelectCurrentUserNode"},
                {"name": "increment_counter", "type": "IncrementCounterNode"},
            ],
            "edges": [
                ["START", "prepare_iteration"],
                ["select_current_user", "increment_counter"],
            ],
            "conditional_edges": [
                {
                    "source": "prepare_iteration",
                    "mapping": {"continue": "select_current_user", "exit": "END"},
                },
                {
                    "source": "increment_counter",
                    "mapping": {"continue": "select_current_user", "exit": "END"},
                },
            ],
        },
    }

    mermaid = _mermaid_from_graph(graph)
    assert (
        "root__node__prepare_iteration -. continue .-> "
        "root__node__select_current_user;" in mermaid
    )
    assert "root__node__prepare_iteration -. exit .-> root__end;" in mermaid
    assert (
        "root__node__increment_counter -. continue .-> "
        "root__node__select_current_user;" in mermaid
    )
    assert "root__node__increment_counter -. exit .-> root__end;" in mermaid


def test_workflow_mermaid_with_agent_workflow_tool_subgraph() -> None:
    """Nested AgentNode workflow tools should render as Mermaid subgraphs."""
    from orcheo_sdk.cli.workflow import _mermaid_from_graph

    graph = {
        "format": "langgraph-script",
        "summary": {
            "nodes": [
                {
                    "name": "agent",
                    "type": "AgentNode",
                    "workflow_tools": [
                        {
                            "name": "search",
                            "graph": {
                                "type": "StateGraph",
                                "summary": {
                                    "nodes": [{"name": "lookup", "type": "TaskNode"}],
                                    "edges": [
                                        ["START", "lookup"],
                                        ["lookup", "END"],
                                    ],
                                    "conditional_edges": [],
                                },
                            },
                        }
                    ],
                }
            ],
            "edges": [["START", "agent"], ["agent", "END"]],
            "conditional_edges": [],
        },
    }

    mermaid = _mermaid_from_graph(graph)
    assert 'subgraph root__agent__tool__search__subgraph["search"]' in mermaid
    assert "root__node__agent -.-> root__agent__tool__search__start;" in mermaid
    assert 'root__agent__tool__search__node__lookup["lookup"]:::tool' in mermaid
    assert 'root__start(["START"]):::first' in mermaid
    assert 'root__end(["END"]):::last' in mermaid


def test_workflow_mermaid_node_identifier_none() -> None:
    """Test node identifier returns None for nodes without id/name/label/type."""
    from orcheo_sdk.cli.workflow import _node_identifier

    # Test with mapping with None value
    result = _node_identifier({"other_field": "value"})
    assert result is None

    # Test with None node
    result = _node_identifier(None)
    assert result is None


def test_workflow_mermaid_identity_state() -> None:
    """Test _identity_state function returns state unchanged."""
    from orcheo_sdk.cli.workflow import _identity_state

    state = {"key": "value", "nested": {"data": 123}}
    result = _identity_state(state, "arg1", "arg2", kwarg="test")
    assert result == state
    assert result is state


def test_workflow_mermaid_collect_node_names_with_none_identifier() -> None:
    """Test _collect_node_names skips nodes with None identifier."""
    from orcheo_sdk.cli.workflow import _collect_node_names

    nodes = [
        {"id": "node1"},
        {"other": "field"},  # No id/name/label/type
        None,  # None node
        {"id": "node2"},
    ]
    names = _collect_node_names(nodes)
    assert names == {"node1", "node2"}


def test_workflow_mermaid_resolve_edge_with_wrong_length_list() -> None:
    """Test _resolve_edge returns None for list with wrong length."""
    from orcheo_sdk.cli.workflow import _resolve_edge

    # List with 1 element
    result = _resolve_edge(["single"])
    assert result is None

    # List with 3 elements
    result = _resolve_edge(["a", "b", "c"])
    assert result is None

    # Empty list
    result = _resolve_edge([])
    assert result is None


def test_workflow_mermaid_resolve_edge_with_non_sequence_non_mapping() -> None:
    """Test _resolve_edge returns None for non-sequence non-mapping input."""
    from orcheo_sdk.cli.workflow import _resolve_edge

    # Integer
    result = _resolve_edge(123)
    assert result is None

    # Float
    result = _resolve_edge(45.6)
    assert result is None

    # Boolean
    result = _resolve_edge(True)
    assert result is None


def test_workflow_mermaid_resolve_edge_with_missing_source_or_target() -> None:
    """Test _resolve_edge returns None when source or target is missing."""
    from orcheo_sdk.cli.workflow import _resolve_edge

    # Mapping with empty source
    result = _resolve_edge({"from": "", "to": "target"})
    assert result is None

    # Mapping with empty target
    result = _resolve_edge({"from": "source", "to": ""})
    assert result is None

    # Mapping with None source
    result = _resolve_edge({"from": None, "to": "target"})
    assert result is None


def test_workflow_mermaid_register_endpoint_with_start_and_end() -> None:
    """Test _register_endpoint does not add START or END to node_names."""
    from orcheo_sdk.cli.workflow import _register_endpoint

    node_names: set[str] = set()

    _register_endpoint(node_names, "START")
    assert "START" not in node_names

    _register_endpoint(node_names, "start")
    assert "start" not in node_names

    _register_endpoint(node_names, "END")
    assert "END" not in node_names

    _register_endpoint(node_names, "end")
    assert "end" not in node_names

    _register_endpoint(node_names, "regular_node")
    assert "regular_node" in node_names


def test_workflow_mermaid_normalise_vertex_with_start() -> None:
    """Test _normalise_vertex converts 'START' to start sentinel."""
    from langgraph.graph import END, START
    from orcheo_sdk.cli.workflow import _normalise_vertex

    result = _normalise_vertex("START", START, END)
    assert result is START

    result = _normalise_vertex("start", START, END)
    assert result is START


def test_workflow_mermaid_normalise_vertex_with_end() -> None:
    """Test _normalise_vertex converts 'END' to end sentinel."""
    from langgraph.graph import END, START
    from orcheo_sdk.cli.workflow import _normalise_vertex

    result = _normalise_vertex("END", START, END)
    assert result is END

    result = _normalise_vertex("end", START, END)
    assert result is END


def test_workflow_mermaid_no_edges_with_nodes() -> None:
    """Test mermaid generation when there are nodes but no edges."""
    from orcheo_sdk.cli.workflow import _compiled_mermaid

    graph = {"nodes": [{"id": "node1"}, {"id": "node2"}], "edges": []}
    mermaid = _compiled_mermaid(graph)
    # Should add START -> first node edge automatically
    assert "node1" in mermaid


def test_workflow_mermaid_no_edges_no_nodes() -> None:
    """Test mermaid generation when there are no nodes and no edges."""
    from orcheo_sdk.cli.workflow import _compiled_mermaid

    graph = {"nodes": [], "edges": []}
    mermaid = _compiled_mermaid(graph)
    # Should add START -> END edge
    assert mermaid is not None


def test_workflow_mermaid_edges_without_start() -> None:
    """Test mermaid generation when edges exist but none start from START."""
    from orcheo_sdk.cli.workflow import _compiled_mermaid

    graph = {
        "nodes": [{"id": "node1"}, {"id": "node2"}, {"id": "node3"}],
        "edges": [{"from": "node1", "to": "node2"}, {"from": "node2", "to": "node3"}],
    }
    mermaid = _compiled_mermaid(graph)
    # Should add START -> node1 edge (first node not in targets)
    assert mermaid is not None


def test_workflow_mermaid_edges_without_start_all_nodes_are_targets() -> None:
    """Test mermaid fallback when all nodes are targets (circular or no entry)."""
    from orcheo_sdk.cli.workflow import _compiled_mermaid

    graph = {
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],  # Circular
    }
    mermaid = _compiled_mermaid(graph)
    # Should add START -> first edge source as fallback
    assert mermaid is not None


def test_workflow_mermaid_with_complex_edge_scenarios() -> None:
    """Test mermaid generation with various complex edge scenarios."""
    from orcheo_sdk.cli.workflow import _compiled_mermaid

    # Test with multiple nodes and complex routing
    graph = {
        "nodes": [
            {"id": "start_node"},
            {"id": "middle_node"},
            {"id": "end_node"},
        ],
        "edges": [
            {"from": "START", "to": "start_node"},
            {"from": "start_node", "to": "middle_node"},
            {"from": "middle_node", "to": "end_node"},
            {"from": "end_node", "to": "END"},
        ],
    }
    mermaid = _compiled_mermaid(graph)
    assert mermaid is not None
    assert "start_node" in mermaid
    assert "middle_node" in mermaid
    assert "end_node" in mermaid


def test_workflow_mermaid_parallel_branches() -> None:
    """Parallel START branches should still produce Mermaid output."""
    from orcheo_sdk.cli.workflow import _compiled_mermaid

    graph = {
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [
            {"from": "START", "to": "a"},
            {"from": "START", "to": "b"},
            {"from": "a", "to": "END"},
            {"from": "b", "to": "END"},
        ],
    }

    mermaid = _compiled_mermaid(graph)
    assert "__start__ --> a" in mermaid
    assert "__start__ --> b" in mermaid
    assert "a --> __end__" in mermaid
    assert "b --> __end__" in mermaid
    assert "classDef default fill:#f2f0ff,line-height:1.2" in mermaid
    assert "classDef first fill:#bfb6fc" in mermaid
    assert "classDef last fill:#bfb6fc" in mermaid


def test_workflow_mermaid_top_level_conditional_edges() -> None:
    """Conditional edges should be rendered when present at graph top level."""
    from orcheo_sdk.cli.workflow import _compiled_mermaid

    graph = {
        "nodes": [{"id": "decision"}, {"id": "left"}, {"id": "right"}],
        "edges": [{"from": "START", "to": "decision"}],
        "conditional_edges": [
            {
                "source": "decision",
                "mapping": {"true": "left", "false": "right"},
                "default": "END",
            }
        ],
    }

    mermaid = _compiled_mermaid(graph)
    assert "__start__ --> decision" in mermaid
    assert "decision --> left" in mermaid
    assert "decision --> right" in mermaid
    assert "decision --> __end__" in mermaid


def test_collect_conditional_edges_skips_non_mapping_branch() -> None:
    """Non-mapping branches are skipped."""
    from orcheo_sdk.cli.workflow.mermaid import _collect_conditional_edges

    node_names: set[str] = set()
    result = _collect_conditional_edges(["not-a-mapping", 42], node_names)
    assert result == []
    assert node_names == set()


def test_collect_conditional_edges_skips_branch_without_source() -> None:
    """Branches missing both 'source' and 'from' are skipped."""
    from orcheo_sdk.cli.workflow.mermaid import _collect_conditional_edges

    node_names: set[str] = set()
    result = _collect_conditional_edges(
        [{"mapping": {"ok": "target"}}],
        node_names,
    )
    assert result == []
    assert node_names == set()


def test_collect_conditional_edges_non_mapping_mapping_field() -> None:
    """When the 'mapping' field is not a Mapping, only the default target is used."""
    from orcheo_sdk.cli.workflow.mermaid import _collect_conditional_edges

    node_names: set[str] = set()
    result = _collect_conditional_edges(
        [{"source": "decision", "mapping": "not-a-dict", "default": "fallback"}],
        node_names,
    )
    assert len(result) == 1
    assert result[0] == ("decision", "fallback")


def test_collect_conditional_edges_skips_unresolvable_target() -> None:
    """Targets that _resolve_edge cannot resolve are skipped."""
    from orcheo_sdk.cli.workflow.mermaid import _collect_conditional_edges

    node_names: set[str] = set()
    # mapping value is empty string → _resolve_edge returns None
    result = _collect_conditional_edges(
        [{"source": "src", "mapping": {"key": ""}}],
        node_names,
    )
    assert result == []


def test_deduplicate_edges_removes_duplicates() -> None:
    """Duplicate edges are removed, preserving first occurrence order."""
    from orcheo_sdk.cli.workflow.mermaid import _deduplicate_edges

    edges = [("a", "b"), ("c", "d"), ("a", "b"), ("c", "d"), ("e", "f")]
    result = _deduplicate_edges(edges)
    assert result == [("a", "b"), ("c", "d"), ("e", "f")]


def test_workflow_mermaid_fallback_skips_sentinel_node_definitions(
    monkeypatch,
) -> None:
    """Fallback renderer ignores sentinel labels if sorted output includes them."""
    from orcheo_sdk.cli.workflow import mermaid as mermaid_module

    original_sorted = sorted

    def _sorted_with_sentinels(values: set[str]) -> list[str]:
        return ["__start__", "__end__", *original_sorted(values)]

    monkeypatch.setattr(
        mermaid_module,
        "sorted",
        _sorted_with_sentinels,
        raising=False,
    )

    mermaid = mermaid_module._render_mermaid_fallback([("node1", "node2")], {"node1"})

    assert "\t__start__(__start__)" not in mermaid
    assert "\t__end__(__end__)" not in mermaid
    assert "\tnode1(node1)" in mermaid
