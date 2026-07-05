"""Tests for workflow Mermaid rendering helpers."""

from __future__ import annotations

import textwrap
from types import SimpleNamespace

import pytest

from orcheo.workflow.mermaid import (
    _render_mermaid_from_script,
    _render_mermaid_from_script_full_env,
    render_mermaid_from_graph_payload,
    render_mermaid_from_graph_payload_full_env,
    render_mermaid_from_ir,
)


_IR_WORKFLOW = textwrap.dedent(
    """
    from orcheo.graph import StateGraph, START, END
    from orcheo.graph.state import State
    from orcheo.nodes.logic import SetVariableNode
    from orcheo.nodes import CodeNode

    class Doubler(CodeNode):
        factor: int = 2

        async def run(self, state, config):
            value = state["node_results"]["setter"]["value"]
            return {"doubled": value * self.factor}

    async def orcheo_workflow() -> StateGraph:
        graph = StateGraph(State)
        graph.add_node(
            "setter", SetVariableNode(name="setter", variables={"value": 10})
        )
        graph.add_node("doubler", Doubler(name="doubler", factor=5))
        graph.add_edge(START, "setter")
        graph.add_edge("setter", "doubler")
        graph.add_edge("doubler", END)
        return graph
    """
)


def _compile_ir_dict() -> dict:
    """Compile the sample workflow to a JSON-coercible frozen-IR mapping."""
    from orcheo.graph.ir import compile_workflow_to_ir

    return compile_workflow_to_ir(_IR_WORKFLOW).model_dump()


def test_render_mermaid_from_graph_payload_rejects_wrong_format() -> None:
    """Non-langgraph-script payloads return None immediately."""
    payload = {"format": "unknown", "source": "anything"}
    assert render_mermaid_from_graph_payload(payload) is None


def test_render_mermaid_from_graph_payload_rejects_blank_source() -> None:
    """Blank script sources should be rejected before any rendering work."""
    payload = {"format": "langgraph-script", "source": "   "}

    assert render_mermaid_from_graph_payload(payload) is None


def test_render_mermaid_from_graph_payload_renders_valid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid payload is forwarded to the sandboxed renderer."""
    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script",
        lambda source, entrypoint=None: SimpleNamespace(
            source=source,
            entrypoint=entrypoint,
        ),
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary.summarise_state_graph",
        lambda graph: {"nodes": []},
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.has_workflow_tool_subgraphs",
        lambda summary: False,
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary._render_compact_mermaid",
        lambda graph: "compact-mermaid",
    )

    payload = {"format": "langgraph-script", "source": "x = 1", "entrypoint": "build"}
    assert render_mermaid_from_graph_payload(payload) == "compact-mermaid"


def test_render_mermaid_from_script_returns_summary_mermaid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow-tool subgraphs should route through the summary renderer."""
    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script",
        lambda source, entrypoint=None: SimpleNamespace(
            source=source,
            entrypoint=entrypoint,
        ),
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary.summarise_state_graph",
        lambda graph: {"nodes": [{"name": "agent", "type": "AgentNode"}]},
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.has_workflow_tool_subgraphs",
        lambda summary: True,
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.render_summary_mermaid",
        lambda summary: "summary-mermaid",
    )

    assert _render_mermaid_from_script("print('hello')") == "summary-mermaid"


def test_render_mermaid_from_script_returns_none_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceptions during sandboxed rendering are caught and None is returned."""

    def _failing_loader(source: str, entrypoint: str | None = None) -> None:
        raise RuntimeError("sandbox error")

    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script",
        _failing_loader,
    )

    assert _render_mermaid_from_script("bad script") is None


def test_render_mermaid_from_graph_payload_full_env_rejects_wrong_format() -> None:
    """Non-langgraph-script payloads return None without attempting execution."""
    payload = {"format": "unknown", "source": "anything"}
    assert render_mermaid_from_graph_payload_full_env(payload) is None


def test_render_mermaid_from_graph_payload_full_env_rejects_blank_source() -> None:
    """Blank sources return None without attempting execution."""
    payload = {"format": "langgraph-script", "source": "   "}
    assert render_mermaid_from_graph_payload_full_env(payload) is None


def test_render_mermaid_from_graph_payload_full_env_returns_mermaid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid payload renders mermaid using the full-env loader."""
    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script_full_env",
        lambda source, entrypoint=None: SimpleNamespace(
            source=source,
            entrypoint=entrypoint,
        ),
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary.summarise_state_graph",
        lambda graph: {"nodes": []},
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.has_workflow_tool_subgraphs",
        lambda summary: False,
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary._render_compact_mermaid",
        lambda graph: "compact-mermaid",
    )

    payload = {"format": "langgraph-script", "source": "x = 1", "entrypoint": None}
    assert render_mermaid_from_graph_payload_full_env(payload) == "compact-mermaid"


def test_render_mermaid_from_script_full_env_returns_summary_mermaid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow-tool subgraphs route through the summary renderer in the full_env path."""
    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script_full_env",
        lambda source, entrypoint=None: SimpleNamespace(
            source=source,
            entrypoint=entrypoint,
        ),
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary.summarise_state_graph",
        lambda graph: {"nodes": [{"name": "agent", "type": "AgentNode"}]},
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.has_workflow_tool_subgraphs",
        lambda summary: True,
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.render_summary_mermaid",
        lambda summary: "summary-mermaid-full-env",
    )

    assert (
        _render_mermaid_from_script_full_env("print('hello')")
        == "summary-mermaid-full-env"
    )


def test_render_mermaid_from_script_full_env_returns_none_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceptions raised during full_env rendering are caught and None is returned."""

    def _failing_loader(source: str, entrypoint: str | None = None) -> None:
        raise RuntimeError("loader failed")

    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script_full_env",
        _failing_loader,
    )

    assert _render_mermaid_from_script_full_env("raise_error()") is None


def test_render_mermaid_from_script_full_env_returns_none_on_script_ingestion_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ScriptIngestionError during full_env rendering is caught and None is returned."""
    from orcheo.graph.ingestion.exceptions import ScriptIngestionError

    def _failing_loader(source: str, entrypoint: str | None = None) -> None:
        raise ScriptIngestionError("script failed to ingest")

    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script_full_env",
        _failing_loader,
    )

    assert _render_mermaid_from_script_full_env("bad_script()") is None


def test_render_mermaid_from_script_renders_conditional_targets() -> None:
    """Conditional workflows render authored branch targets, not detached nodes."""
    source = textwrap.dedent(
        """
        from orcheo.graph import END, START, StateGraph
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node("flag", SetVariableNode(name="flag", variables={"go": True}))
            graph.add_node("yes", SetVariableNode(name="yes", variables={"hit": True}))
            graph.add_node("no", SetVariableNode(name="no", variables={"hit": False}))
            graph.add_edge(START, "flag")
            graph.add_conditional_edges(
                "flag",
                {
                    "path": "node_results.flag.go",
                    "mapping": {"true": "yes", "false": "no"},
                },
            )
            graph.add_edge("yes", END)
            graph.add_edge("no", END)
            return graph
        """
    )

    mermaid = _render_mermaid_from_script_full_env(source)

    assert mermaid is not None
    assert "root__node__flag -. true .-> root__node__yes;" in mermaid
    assert "root__node__flag -. false .-> root__node__no;" in mermaid
    assert "root__node__flag --> root__end;" not in mermaid


def test_render_mermaid_from_ir_renders_builtin_and_code_nodes() -> None:
    """A frozen IR renders both built-in and CodeNode nodes plus their edges."""
    mermaid = render_mermaid_from_ir(_compile_ir_dict())

    assert mermaid is not None
    assert "setter" in mermaid
    assert "doubler" in mermaid
    assert "setter --> doubler" in mermaid


def test_render_mermaid_from_ir_expands_nested_subgraph_node() -> None:
    """Frozen IR Mermaid rendering expands nested graph nodes."""
    from orcheo.graph.ir import compile_workflow_to_ir

    source = textwrap.dedent(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            child = StateGraph(State)
            child.add_node("inner", SetVariableNode(name="inner", variables={"x": 1}))
            child.add_edge(START, "inner")
            child.add_edge("inner", END)

            graph = StateGraph(State)
            graph.add_node("branch", child.compile())
            graph.add_edge(START, "branch")
            graph.add_edge("branch", END)
            return graph
        """
    )

    mermaid = render_mermaid_from_ir(compile_workflow_to_ir(source).model_dump())

    assert mermaid is not None
    assert 'subgraph root__branch__subgraph__subgraph["branch"]' in mermaid
    assert "root__branch__subgraph__node__inner" in mermaid
    assert "root__node__branch[" not in mermaid


def test_render_mermaid_from_ir_expands_agent_workflow_tool() -> None:
    """Frozen IR Mermaid rendering expands AgentNode workflow tools."""
    from orcheo.graph.ir import compile_workflow_to_ir

    source = textwrap.dedent(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.ai import AgentNode, WorkflowTool
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            lookup = StateGraph(State)
            lookup.add_node(
                "set_context",
                SetVariableNode(name="set_context", variables={"answer": "ok"}),
            )
            lookup.add_edge(START, "set_context")
            lookup.add_edge("set_context", END)

            graph = StateGraph(State)
            graph.add_node(
                "agent",
                AgentNode(
                    name="agent",
                    ai_model="gpt-4o-mini",
                    workflow_tools=[
                        WorkflowTool(
                            name="lookup",
                            description="Look up context",
                            graph=lookup,
                        )
                    ],
                ),
            )
            graph.add_edge(START, "agent")
            graph.add_edge("agent", END)
            return graph
        """
    )

    mermaid = render_mermaid_from_ir(compile_workflow_to_ir(source).model_dump())

    assert mermaid is not None
    assert 'subgraph root__agent__tool__lookup__subgraph["lookup"]' in mermaid
    assert "root__agent__tool__lookup__node__set_context" in mermaid
    assert "root__node__agent -.-> root__agent__tool__lookup__start;" in mermaid


def test_render_mermaid_from_ir_does_not_execute_code_node_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendering an IR must never import or invoke the sandbox runner."""
    import orcheo.sandbox.code_node as code_node_module

    def _boom(*_: object, **__: object) -> None:
        raise AssertionError("CodeNode body must not be built during rendering")

    monkeypatch.setattr(
        code_node_module, "build_sandboxed_state_graph", _boom, raising=True
    )

    assert render_mermaid_from_ir(_compile_ir_dict()) is not None


def test_render_mermaid_from_ir_returns_none_for_malformed_ir() -> None:
    """A malformed IR mapping is caught and yields None."""
    assert render_mermaid_from_ir({"nodes": "not-a-list"}) is None


def test_ir_diagram_placeholder_is_an_inert_passthrough() -> None:
    """The CodeNode diagram placeholder returns state unchanged, ignoring extras."""
    from orcheo.workflow.mermaid import _ir_diagram_placeholder

    placeholder = _ir_diagram_placeholder(object())
    state = {"node_results": {"x": 1}}

    assert placeholder(state) is state
    assert placeholder(state, {"configurable": {}}, extra="ignored") is state


def test_render_mermaid_from_graph_payload_renders_frozen_ir() -> None:
    """A stored frozen-IR payload routes through the IR renderer."""
    payload = {"format": "frozen-ir", "ir": _compile_ir_dict(), "entrypoint": "setter"}

    mermaid = render_mermaid_from_graph_payload(payload)

    assert mermaid is not None
    assert "doubler" in mermaid


def test_render_mermaid_from_graph_payload_full_env_renders_frozen_ir() -> None:
    """The full-env entrypoint also renders frozen-IR payloads."""
    payload = {"format": "frozen-ir", "ir": _compile_ir_dict(), "entrypoint": "setter"}

    mermaid = render_mermaid_from_graph_payload_full_env(payload)

    assert mermaid is not None
    assert "setter" in mermaid


def test_render_mermaid_from_graph_payload_frozen_ir_requires_ir_mapping() -> None:
    """A frozen-IR payload with a non-mapping ``ir`` field returns None."""
    payload = {"format": "frozen-ir", "ir": "not-a-mapping"}

    assert render_mermaid_from_graph_payload(payload) is None
