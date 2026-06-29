"""Tests for the restricted-AST interpreter (Tasks 2.3-2.7)."""

from __future__ import annotations
import builtins
import textwrap
import pytest
from orcheo.graph.ir.exceptions import WorkflowValidationError
from orcheo.graph.ir.interpreter import compile_workflow_to_ir
from orcheo.graph.ir.models import BuiltinNodeSpec, CodeNodeSpec


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
