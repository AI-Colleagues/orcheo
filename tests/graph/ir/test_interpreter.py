"""Tests for the restricted-AST interpreter (Tasks 2.3-2.7)."""

from __future__ import annotations
import builtins
import textwrap
import pytest
from orcheo.graph.ir.exceptions import WorkflowValidationError
from orcheo.graph.ir.interpreter import compile_workflow_to_ir
from orcheo.graph.ir.models import (
    IR_CONFIG_KIND_KEY,
    WORKFLOW_TOOL_CONFIG_KIND,
    BuiltinNodeSpec,
    CodeNodeSpec,
    SubgraphNodeSpec,
)


CONFORMING = '''
"""A conforming workflow."""

from orcheo.graph import StateGraph, START, END
from orcheo.graph.state import State
from orcheo.nodes.logic import SetVariableNode
from orcheo.nodes import CodeNode


class Verdict(CodeNode):
    threshold: int = 8

    async def run(self, state, config):
        score = state["results"]["setter"]["value"]
        return {"results": {"verdict": "pass" if score >= self.threshold else "fail"}}


async def orcheo_workflow() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node("setter", SetVariableNode(name="setter", variables={"value": 9}))
    graph.add_node("verdict", Verdict(name="verdict", threshold=8))
    graph.add_edge(START, "setter")
    graph.add_edge("setter", "verdict")
    graph.add_conditional_edges(
        "verdict",
        {"path": "results.verdict", "mapping": {"pass": "setter", "fail": END}},
    )
    return graph
'''


def _compile(source: str):
    """Compile a dedented source string into an IR."""
    return compile_workflow_to_ir(textwrap.dedent(source))


def test_interprets_conforming_workflow() -> None:
    """A conforming script compiles to the expected IR."""
    ir = _compile(CONFORMING)

    assert ir.entrypoint == "setter"
    setter, verdict = ir.nodes
    assert isinstance(setter, BuiltinNodeSpec)
    assert setter.type == "SetVariableNode"
    assert setter.config == {"variables": {"value": 9}}
    assert isinstance(verdict, CodeNodeSpec)
    assert verdict.config == {"threshold": 8}
    assert verdict.injected == ["threshold"]
    assert "self.threshold" in verdict.body
    assert [(e.source, e.target) for e in ir.edges] == [
        ("__start__", "setter"),
        ("setter", "verdict"),
    ]
    cond = ir.conditional_edges[0]
    assert cond.source == "verdict"
    assert cond.mapping == {"pass": "setter", "fail": "__end__"}


def test_round_trips_as_json() -> None:
    """The interpreted IR round-trips through JSON."""
    from orcheo.graph.ir.models import GraphIR

    ir = _compile(CONFORMING)
    assert GraphIR.model_validate_json(ir.model_dump_json()) == ir


def test_ingestion_executes_no_author_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compilation never calls ``exec``/``eval`` on author source."""

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ingestion must not exec/eval author code")

    monkeypatch.setattr(builtins, "exec", _boom)
    monkeypatch.setattr(builtins, "eval", _boom)

    ir = _compile(CONFORMING)
    assert len(ir.nodes) == 2


def test_variable_assigned_node_is_resolved() -> None:
    """A node assigned to a variable before ``add_node`` is resolved."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            graph = StateGraph(State)
            setter = SetVariableNode(name="setter", variables={"x": 1})
            graph.add_node("setter", setter)
            graph.add_edge(START, "setter")
            graph.add_edge("setter", END)
            return graph
        """
    )

    assert ir.nodes[0].id == "setter"
    assert ir.nodes[0].type == "SetVariableNode"


def test_nested_subgraph_node_is_preserved_in_ir() -> None:
    """A nested ``StateGraph`` compiles to a subgraph node, not a flattened IR."""
    ir = _compile(
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

    assert ir.entrypoint == "branch"
    assert len(ir.nodes) == 1
    branch = ir.nodes[0]
    assert isinstance(branch, SubgraphNodeSpec)
    assert branch.id == "branch"
    assert branch.graph.entrypoint == "inner"
    assert [node.id for node in branch.graph.nodes] == ["inner"]


def test_unreferenced_secondary_graph_is_not_flattened_into_root() -> None:
    """Only the returned graph becomes the root IR."""
    ir = _compile(
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
            graph.add_node("outer", SetVariableNode(name="outer", variables={"y": 2}))
            graph.add_edge(START, "outer")
            graph.add_edge("outer", END)
            return graph
        """
    )

    assert [node.id for node in ir.nodes] == ["outer"]
    assert ir.entrypoint == "outer"


def test_agent_workflow_tool_graph_is_preserved_in_builtin_config() -> None:
    """AgentNode workflow tools can carry restricted nested graph IR."""
    ir = _compile(
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
                            return_direct=True,
                        )
                    ],
                ),
            )
            graph.add_edge(START, "agent")
            graph.add_edge("agent", END)
            return graph
        """
    )

    agent = ir.nodes[0]
    assert isinstance(agent, BuiltinNodeSpec)
    tool = agent.config["workflow_tools"][0]
    assert tool[IR_CONFIG_KIND_KEY] == WORKFLOW_TOOL_CONFIG_KIND
    assert tool["name"] == "lookup"
    assert tool["return_direct"] is True
    assert tool["graph"]["entrypoint"] == "set_context"


def test_agent_workflow_tool_args_schema_is_rejected() -> None:
    """Restricted workflow tools intentionally do not support dynamic schemas yet."""
    with pytest.raises(WorkflowValidationError, match="args_schema"):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State
            from orcheo.nodes.ai import AgentNode, WorkflowTool
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                lookup = StateGraph(State)
                lookup.add_node("set_context", SetVariableNode(name="set_context"))
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
                                args_schema=dict,
                            )
                        ],
                    ),
                )
                graph.add_edge(START, "agent")
                graph.add_edge("agent", END)
                return graph
            """
        )


def test_set_entry_point_resolves_entrypoint() -> None:
    """``set_entry_point`` is used when no explicit START edge exists."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node("setter", SetVariableNode(name="setter", variables={"x": 1}))
            graph.set_entry_point("setter")
            graph.add_edge("setter", END)
            return graph
        """
    )

    assert ir.entrypoint == "setter"


def test_unknown_node_type_rejected_at_ingest() -> None:
    """An unregistered node type is rejected with a line reference."""
    with pytest.raises(
        WorkflowValidationError, match="unknown node type 'NotARealNode'"
    ):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State
            from orcheo.nodes import NotARealNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("x", NotARealNode(name="x"))
                graph.add_edge(START, "x")
                graph.add_edge("x", END)
                return graph
            """
        )


def test_credential_in_code_node_config_rejected() -> None:
    """A ``[[credential]]`` in CodeNode config is rejected at ingest."""
    with pytest.raises(WorkflowValidationError, match="not allowed in CodeNode config"):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State
            from orcheo.nodes import CodeNode

            class Secret(CodeNode):
                async def run(self, state, config):
                    return {"results": {"x": self.token}}

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("s", Secret(name="s", token="[[api_key]]"))
                graph.add_edge(START, "s")
                graph.add_edge("s", END)
                return graph
            """
        )


def test_credential_in_builtin_config_allowed() -> None:
    """A ``[[credential]]`` in built-in node config is accepted at ingest."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node(
                "s", SetVariableNode(name="s", variables={"token": "[[api_key]]"})
            )
            graph.add_edge(START, "s")
            graph.add_edge("s", END)
            return graph
        """
    )

    assert ir.nodes[0].config == {"variables": {"token": "[[api_key]]"}}


def test_gadget_chain_script_rejected() -> None:
    """A gadget-chain payload is rejected during compilation."""
    with pytest.raises(WorkflowValidationError):
        _compile(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                graph.add_node("x", ().__class__.__bases__)
                return graph
            """
        )


def test_lambda_node_script_rejected() -> None:
    """A lambda node payload is rejected during compilation."""
    with pytest.raises(WorkflowValidationError, match="lambdas"):
        _compile(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                graph.add_node("x", lambda s: s)
                return graph
            """
        )


def test_code_body_unsupported_construct_rejected() -> None:
    """A CodeNode body using ``await`` is rejected during compilation."""
    with pytest.raises(WorkflowValidationError, match="await"):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State
            from orcheo.nodes import CodeNode

            class Bad(CodeNode):
                async def run(self, state, config):
                    data = await fetch()
                    return {"results": {"x": data}}

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("b", Bad(name="b"))
                graph.add_edge(START, "b")
                graph.add_edge("b", END)
                return graph
            """
        )
