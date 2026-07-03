"""Tests for the restricted-AST interpreter (Tasks 2.3-2.7)."""

from __future__ import annotations
import builtins
import textwrap
from pathlib import Path
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

_REPO_ROOT = Path(__file__).resolve().parents[3]
_KNOWLEDGE_GUIDE_WORKFLOW = (
    _REPO_ROOT
    / "colleague-experts/colleague-candidates/colleagues/knowledge_desk/"
    / "knowledge_guide/workflow.py"
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
        return {"verdict": "pass" if score >= self.threshold else "fail"}


async def orcheo_workflow() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node("setter", SetVariableNode(name="setter", variables={"value": 9}))
    graph.add_node("verdict", Verdict(name="verdict", threshold=8))
    graph.add_edge(START, "setter")
    graph.add_edge("setter", "verdict")
    graph.add_conditional_edges(
        "verdict",
        {"path": "results.verdict.verdict", "mapping": {"pass": "setter", "fail": END}},
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


def test_workflow_tool_args_schema_is_lowered_to_json_schema() -> None:
    """Workflow tools may reference restricted schema classes for input shape."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.ai import AgentNode, WorkflowTool
        from orcheo.nodes.logic import SetVariableNode
        from orcheo.schema import BaseModel, Field

        class LookupInput(BaseModel):
            query: str = Field(description="User question")

        def build_lookup():
            lookup = StateGraph(State)
            lookup.add_node("set_context", SetVariableNode(name="set_context"))
            lookup.add_edge(START, "set_context")
            lookup.add_edge("set_context", END)
            return lookup

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node(
                "agent",
                AgentNode(
                    name="agent",
                    ai_model="gpt-4o-mini",
                    workflow_tools=[
                        {
                            "name": "lookup",
                            "description": "Look up context",
                            "graph": build_lookup(),
                            "args_schema": LookupInput,
                            "output_path": "results.set_context",
                        }
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
    assert tool["name"] == "lookup"
    assert tool["output_path"] == "results.set_context"
    assert tool["graph"]["entrypoint"] == "set_context"
    assert tool["args_schema"]["type"] == "object"
    assert tool["args_schema"]["properties"]["query"]["type"] == "string"
    assert tool["args_schema"]["required"] == ["query"]


def test_knowledge_guide_workflow_compiles_in_restricted_mode() -> None:
    """The Knowledge Guide workflow compiles with helper graphs and schema imports."""
    ir = compile_workflow_to_ir(_KNOWLEDGE_GUIDE_WORKFLOW.read_text())

    agent = ir.nodes[0]
    assert isinstance(agent, BuiltinNodeSpec)
    tool = agent.config["workflow_tools"][0]
    assert tool["name"] == "mongodb_hybrid_search"
    assert tool["graph"]["entrypoint"] == "query_embedding"
    assert tool["args_schema"]["properties"]["query"]["type"] == "string"


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


def test_set_finish_point_emits_end_edge() -> None:
    """``set_finish_point`` is preserved as a ``node -> END`` edge in the IR."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node("setter", SetVariableNode(name="setter", variables={"x": 1}))
            graph.set_entry_point("setter")
            graph.set_finish_point("setter")
            return graph
        """
    )

    assert ir.entrypoint == "setter"
    assert [(e.source, e.target) for e in ir.edges] == [("setter", "__end__")]


def test_statements_after_return_are_ignored() -> None:
    """Unreachable graph mutations after ``return`` are not folded into the IR."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node("setter", SetVariableNode(name="setter", variables={"x": 1}))
            graph.add_edge(START, "setter")
            graph.add_edge("setter", END)
            return graph
            graph.add_node("hidden", SetVariableNode(name="hidden", variables={"y": 2}))
            graph.add_edge("setter", "hidden")
        """
    )

    assert [node.id for node in ir.nodes] == ["setter"]
    assert all(edge.target != "hidden" for edge in ir.edges)


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
                    return {"x": self.token}

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
                    return {"x": data}

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("b", Bad(name="b"))
                graph.add_edge(START, "b")
                graph.add_edge("b", END)
                return graph
            """
        )


def test_syntax_error_is_reported() -> None:
    """Unparsable source surfaces a line-referenced validation error."""
    with pytest.raises(WorkflowValidationError, match="could not parse") as exc_info:
        _compile("def orcheo_workflow(:\n    pass\n")

    assert exc_info.value.lineno is not None


def test_collects_all_class_field_kinds_and_trailing_members() -> None:
    """Annotation-only, default, and assign fields (incl. after ``run``) are collected."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes import CodeNode

        class Multi(CodeNode):
            "a docstring member that the collector skips"
            a: int = 1
            b: int
            c = 3

            async def run(self, state, config):
                return {"v": self.a}

            d: int = 4

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node("m", Multi(name="m"))
            graph.add_edge(START, "m")
            graph.add_edge("m", END)
            return graph
        """
    )

    node = ir.nodes[0]
    assert isinstance(node, CodeNodeSpec)
    # All declared fields are injected; only those with defaults carry config.
    assert node.injected == ["a", "b", "c", "d"]
    assert node.config == {"a": 1, "c": 3, "d": 4}


def test_entrypoint_without_return_is_rejected() -> None:
    """An entrypoint that never returns the graph is rejected."""
    with pytest.raises(
        WorkflowValidationError, match="must return the assembled graph"
    ):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("s", SetVariableNode(name="s", variables={"x": 1}))
                graph.add_edge(START, "s")
                graph.add_edge("s", END)
            """
        )


def test_duplicate_graph_variable_is_rejected() -> None:
    """Re-assigning a graph variable from StateGraph(...) is rejected."""
    with pytest.raises(WorkflowValidationError, match="assigned more than once"):
        _compile(
            """
            from orcheo.graph import StateGraph
            from orcheo.graph.state import State

            def orcheo_workflow():
                graph = StateGraph(State)
                graph = StateGraph(State)
                return graph
            """
        )


def test_entrypoint_docstring_is_ignored() -> None:
    """A docstring statement inside the entrypoint is skipped during interpretation."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            "build the graph"
            graph = StateGraph(State)
            graph.add_node("s", SetVariableNode(name="s", variables={"x": 1}))
            graph.add_edge(START, "s")
            graph.add_edge("s", END)
            return graph
        """
    )

    assert ir.entrypoint == "s"


def test_returning_non_graph_variable_is_rejected() -> None:
    """Returning a node variable (not a StateGraph) is rejected."""
    with pytest.raises(
        WorkflowValidationError, match="was not assigned from StateGraph"
    ):
        _compile(
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
                return setter
            """
        )


def test_returning_compiled_graph_resolves_root() -> None:
    """``return graph.compile()`` resolves the root graph name."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node("s", SetVariableNode(name="s", variables={"x": 1}))
            graph.add_edge(START, "s")
            graph.add_edge("s", END)
            return graph.compile()
        """
    )

    assert ir.entrypoint == "s"


def test_nested_graph_cycle_is_rejected() -> None:
    """Mutually-referencing nested graphs are rejected as a cycle."""
    with pytest.raises(WorkflowValidationError, match="cycle detected"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State

            def orcheo_workflow():
                a = StateGraph(State)
                b = StateGraph(State)
                a.add_node("nb", b)
                b.add_node("na", a)
                a.add_edge(START, "nb")
                b.add_edge(START, "na")
                return a
            """
        )


def test_workflow_to_ir_rejects_unknown_graph_name() -> None:
    """The internal resolver guards against an unknown nested graph name."""
    from orcheo.graph.ir.interpreter import _workflow_to_ir

    with pytest.raises(WorkflowValidationError, match="unknown nested workflow graph"):
        _workflow_to_ir("missing", {}, stack=[])


def test_method_on_non_graph_variable_is_rejected() -> None:
    """A graph-assembly method invoked on a non-StateGraph variable is rejected."""
    with pytest.raises(WorkflowValidationError, match="not a StateGraph variable"):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                node = SetVariableNode(name="node", variables={"x": 1})
                node.add_edge(START, "x")
                return graph
            """
        )


def test_interpret_graph_call_ignores_unhandled_method() -> None:
    """The dispatcher is a silent no-op for a method outside its table."""
    import ast

    from orcheo.graph.ir.interpreter import (
        _CompileContext,
        _Workflow,
        _interpret_graph_call,
    )

    call = ast.parse("g.unhandled('x')", mode="eval").body
    graphs = {"g": _Workflow()}
    ctx = _CompileContext(
        source="",
        code_classes={},
        graph_builders={},
        schema_classes={},
    )

    _interpret_graph_call(call, ctx, graphs, {}, {"g": set()})

    assert graphs["g"].nodes == []
    assert graphs["g"].edges == []


def test_duplicate_node_id_in_add_node_is_rejected() -> None:
    """Adding two nodes with the same id is rejected during interpretation."""
    with pytest.raises(WorkflowValidationError, match="duplicate node id"):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("dup", SetVariableNode(name="dup", variables={"x": 1}))
                graph.add_node("dup", SetVariableNode(name="dup2", variables={"y": 2}))
                graph.add_edge(START, "dup")
                return graph
            """
        )


def test_node_constructed_from_attribute_is_rejected() -> None:
    """A node built from an attribute call (not a bare class) is rejected."""
    with pytest.raises(WorkflowValidationError, match="constructed from a node class"):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("x", mod.Node())
                graph.add_edge(START, "x")
                return graph
            """
        )


def test_node_with_positional_args_is_rejected() -> None:
    """A node constructed with positional args is rejected."""
    with pytest.raises(WorkflowValidationError, match="keyword arguments only"):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("x", SetVariableNode("pos", name="x"))
                graph.add_edge(START, "x")
                return graph
            """
        )


def test_single_arg_add_node_resolves_name_kwarg() -> None:
    """``add_node(node)`` derives the id from the node's ``name=`` kwarg."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node(SetVariableNode(name="solo", variables={"x": 1}))
            graph.add_edge(START, "solo")
            graph.add_edge("solo", END)
            return graph
        """
    )

    assert ir.nodes[0].id == "solo"


def test_single_arg_add_node_requires_a_constructor() -> None:
    """``add_node(name)`` where the arg is not a constructor is rejected."""
    with pytest.raises(WorkflowValidationError, match="requires a node constructor"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node(undefined)
                graph.add_edge(START, "x")
                return graph
            """
        )


def test_add_node_with_no_args_is_rejected() -> None:
    """``add_node()`` with no arguments is rejected."""
    with pytest.raises(WorkflowValidationError, match="expects \\(id, node\\)"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node()
                graph.add_edge(START, "x")
                return graph
            """
        )


def test_add_node_with_literal_target_is_rejected() -> None:
    """``add_node(id, literal)`` where the node is a bare literal is rejected."""
    with pytest.raises(WorkflowValidationError, match="node instance or nested graph"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("x", 5)
                graph.add_edge(START, "x")
                return graph
            """
        )


def test_nested_compile_with_arguments_is_rejected() -> None:
    """A nested ``child.compile(arg)`` call passing arguments is rejected."""
    with pytest.raises(
        WorkflowValidationError, match="compile\\(\\) calls may not pass arguments"
    ):
        _compile(
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
                graph.add_node("branch", child.compile(5))
                graph.add_edge(START, "branch")
                graph.add_edge("branch", END)
                return graph
            """
        )


def test_single_arg_add_node_requires_name_kwarg() -> None:
    """``add_node(node)`` without a ``name=`` kwarg is rejected."""
    with pytest.raises(WorkflowValidationError, match="requires the node to set name"):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node(SetVariableNode(variables={"x": 1}))
                graph.add_edge(START, "x")
                return graph
            """
        )


def test_node_kwargs_reject_double_star_unpacking() -> None:
    """A node constructed with ``**kwargs`` unpacking is rejected."""
    with pytest.raises(WorkflowValidationError, match="keyword unpacking"):
        _compile(
            """
            from orcheo.graph import StateGraph, START, END
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("x", SetVariableNode(name="x", **other))
                graph.add_edge(START, "x")
                return graph
            """
        )


_AGENT_TOOL_TEMPLATE = """
    from orcheo.graph import StateGraph, START, END
    from orcheo.graph.state import State
    from orcheo.nodes.ai import AgentNode, WorkflowTool
    from orcheo.nodes.logic import SetVariableNode

    def orcheo_workflow():
        lookup = StateGraph(State)
        lookup.add_node("inner", SetVariableNode(name="inner", variables={"x": 1}))
        lookup.add_edge(START, "inner")
        lookup.add_edge("inner", END)

        graph = StateGraph(State)
        graph.add_node(
            "agent",
            AgentNode(name="agent", ai_model="x", workflow_tools=__TOOLS__),
        )
        graph.add_edge(START, "agent")
        graph.add_edge("agent", END)
        return graph
"""


def _agent_tool_workflow(tools_src: str) -> str:
    """Build an AgentNode workflow whose ``workflow_tools`` is ``tools_src``."""
    return _AGENT_TOOL_TEMPLATE.replace("__TOOLS__", tools_src)


def test_workflow_tools_must_be_a_list() -> None:
    """A non-list ``workflow_tools`` value is rejected."""
    with pytest.raises(WorkflowValidationError, match="must be a list of WorkflowTool"):
        _compile(_agent_tool_workflow("5"))


def test_workflow_tools_entry_must_be_workflow_tool() -> None:
    """A ``workflow_tools`` entry that is not a WorkflowTool call is rejected."""
    with pytest.raises(WorkflowValidationError, match="must be WorkflowTool"):
        _compile(_agent_tool_workflow("[5]"))


def test_workflow_tool_rejects_positional_args() -> None:
    """A WorkflowTool with positional arguments is rejected."""
    with pytest.raises(WorkflowValidationError, match="keyword arguments only"):
        _compile(
            _agent_tool_workflow(
                '[WorkflowTool("pos", name="t", description="d", graph=lookup)]'
            )
        )


def test_workflow_tool_graph_must_reference_state_graph() -> None:
    """A WorkflowTool whose graph is not a StateGraph variable is rejected."""
    with pytest.raises(
        WorkflowValidationError, match="must reference a StateGraph variable"
    ):
        _compile(
            _agent_tool_workflow(
                '[WorkflowTool(name="t", description="d", graph=notagraph)]'
            )
        )


def test_workflow_tool_return_direct_must_be_bool() -> None:
    """A WorkflowTool with a non-boolean ``return_direct`` is rejected."""
    with pytest.raises(WorkflowValidationError, match="must be a boolean literal"):
        _compile(
            _agent_tool_workflow(
                '[WorkflowTool(name="t", description="d", graph=lookup, '
                'return_direct="yes")]'
            )
        )


def test_workflow_tool_rejects_double_star_unpacking() -> None:
    """A WorkflowTool constructed with ``**kwargs`` is rejected."""
    with pytest.raises(WorkflowValidationError, match="keyword unpacking"):
        _compile(_agent_tool_workflow("[WorkflowTool(**opts)]"))


def test_workflow_tool_requires_name() -> None:
    """A WorkflowTool missing its required ``name`` kwarg is rejected."""
    with pytest.raises(WorkflowValidationError, match="requires 'name'"):
        _compile(_agent_tool_workflow('[WorkflowTool(description="d", graph=lookup)]'))


def test_keyword_map_rejects_duplicate_keywords() -> None:
    """The keyword collector guards against duplicate keyword names."""
    import ast

    from orcheo.graph.ir.interpreter import _keyword_map

    call = ast.Call(
        func=ast.Name(id="WorkflowTool", ctx=ast.Load()),
        args=[],
        keywords=[
            ast.keyword(arg="name", value=ast.Constant(value="a")),
            ast.keyword(arg="name", value=ast.Constant(value="b")),
        ],
    )

    with pytest.raises(WorkflowValidationError, match="is duplicated"):
        _keyword_map(call, allowed={"name"}, what="WorkflowTool")


def test_add_edge_requires_two_arguments() -> None:
    """``add_edge`` with the wrong arity is rejected."""
    with pytest.raises(WorkflowValidationError, match="add_edge expects exactly"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_edge(START)
                return graph
            """
        )


def test_conditional_edge_requires_source_and_dict() -> None:
    """``add_conditional_edges`` without a config dict is rejected."""
    with pytest.raises(WorkflowValidationError, match="expects \\(source"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_conditional_edges("a")
                return graph
            """
        )


def test_conditional_edge_config_keys_must_be_strings() -> None:
    """A conditional-edge config with a non-string key is rejected."""
    with pytest.raises(WorkflowValidationError, match="config keys must be string"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("a", SetVariableNode(name="a", variables={"x": 1}))
                graph.add_edge(START, "a")
                graph.add_conditional_edges("a", {1: "x"})
                return graph
            """
        )


def test_conditional_edge_unknown_config_key_is_rejected() -> None:
    """A conditional-edge config with an unknown key is rejected."""
    with pytest.raises(
        WorkflowValidationError, match="unknown conditional edge config"
    ):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("a", SetVariableNode(name="a", variables={"x": 1}))
                graph.add_edge(START, "a")
                graph.add_conditional_edges("a", {"bogus": "x"})
                return graph
            """
        )


def test_conditional_edge_requires_path_and_mapping() -> None:
    """A conditional-edge config missing ``mapping`` is rejected."""
    with pytest.raises(WorkflowValidationError, match="requires 'path' and 'mapping'"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("a", SetVariableNode(name="a", variables={"x": 1}))
                graph.add_edge(START, "a")
                graph.add_conditional_edges("a", {"path": "results.a.x"})
                return graph
            """
        )


def test_conditional_edge_mapping_must_be_dict() -> None:
    """A conditional-edge ``mapping`` that is not a dict literal is rejected."""
    with pytest.raises(WorkflowValidationError, match="'mapping' must be a dict"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("a", SetVariableNode(name="a", variables={"x": 1}))
                graph.add_edge(START, "a")
                graph.add_conditional_edges(
                    "a", {"path": "results.a.x", "mapping": "notadict"}
                )
                return graph
            """
        )


def test_conditional_edge_mapping_keys_must_be_strings() -> None:
    """A conditional-edge mapping with a non-string key is rejected."""
    with pytest.raises(WorkflowValidationError, match="mapping keys must be string"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("a", SetVariableNode(name="a", variables={"x": 1}))
                graph.add_edge(START, "a")
                graph.add_conditional_edges(
                    "a", {"path": "results.a.x", "mapping": {1: "x"}}
                )
                return graph
            """
        )


def test_conditional_edge_default_is_resolved() -> None:
    """A conditional-edge config carrying an explicit ``default`` is resolved."""
    ir = _compile(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node("a", SetVariableNode(name="a", variables={"x": 1}))
            graph.add_node("b", SetVariableNode(name="b", variables={"y": 2}))
            graph.add_edge(START, "a")
            graph.add_conditional_edges(
                "a", {"path": "results.a.x", "mapping": {"go": "b"}, "default": "b"}
            )
            graph.add_edge("b", END)
            return graph
        """
    )

    assert ir.conditional_edges[0].default == "b"


def test_edge_endpoints_must_be_strings_or_sentinels() -> None:
    """An edge endpoint that is neither a string nor START/END is rejected."""
    with pytest.raises(WorkflowValidationError, match="must be node-id strings"):
        _compile(
            """
            from orcheo.graph import StateGraph
            from orcheo.graph.state import State

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_edge(123, "x")
                return graph
            """
        )


def test_add_node_id_must_be_string_literal() -> None:
    """A non-string ``add_node`` id is rejected."""
    with pytest.raises(WorkflowValidationError, match="must be a string literal"):
        _compile(
            """
            from orcheo.graph import StateGraph, START
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node(123, SetVariableNode(name="x", variables={"v": 1}))
                graph.add_edge(START, "x")
                return graph
            """
        )


def test_set_entry_point_requires_an_argument() -> None:
    """``set_entry_point`` with no argument is rejected."""
    with pytest.raises(WorkflowValidationError, match="requires a string argument"):
        _compile(
            """
            from orcheo.graph import StateGraph, END
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("s", SetVariableNode(name="s", variables={"x": 1}))
                graph.set_entry_point()
                graph.add_edge("s", END)
                return graph
            """
        )


def test_workflow_without_entrypoint_edge_is_rejected() -> None:
    """A graph with no START edge and no ``set_entry_point`` is rejected."""
    with pytest.raises(WorkflowValidationError, match="has no entrypoint"):
        _compile(
            """
            from orcheo.graph import StateGraph, END
            from orcheo.graph.state import State
            from orcheo.nodes.logic import SetVariableNode

            def orcheo_workflow():
                graph = StateGraph(State)
                graph.add_node("s", SetVariableNode(name="s", variables={"x": 1}))
                graph.add_edge("s", END)
                return graph
            """
        )
