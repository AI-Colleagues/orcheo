"""Tests for the restricted grammar validator (Tasks 2.1, 2.2)."""

from __future__ import annotations
import ast
import textwrap
import pytest
from orcheo.graph.ir.exceptions import WorkflowValidationError
from orcheo.graph.ir.grammar import validate_grammar


def _validate(source: str) -> None:
    """Parse and validate ``source`` against the restricted grammar."""
    validate_grammar(ast.parse(textwrap.dedent(source)))


CONFORMING = '''
"""A conforming workflow."""

from orcheo.graph import StateGraph, START, END
from orcheo.graph.state import State
from orcheo.nodes.logic import SetVariableNode
from orcheo.nodes import CodeNode


class Verdict(CodeNode):
    """Custom logic."""

    threshold: int = 8

    async def run(self, state, config):
        return {"results": {"v": self.threshold}}


async def orcheo_workflow() -> StateGraph:
    """Build the graph."""
    graph = StateGraph(State)
    graph.add_node("setter", SetVariableNode(name="setter", variables={"x": 1}))
    graph.add_node("verdict", Verdict(name="verdict", threshold=8))
    graph.add_edge(START, "setter")
    graph.add_edge("setter", "verdict")
    graph.add_edge("verdict", END)
    return graph
'''


def test_conforming_script_passes() -> None:
    """A fully conforming script validates without error."""
    _validate(CONFORMING)


def test_sync_def_entrypoint_passes() -> None:
    """A synchronous ``def orcheo_workflow`` is accepted."""
    _validate(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode

        def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node("s", SetVariableNode(name="s", variables={"x": 1}))
            graph.add_edge(START, "s")
            graph.add_edge("s", END)
            return graph
        """
    )


def test_non_orcheo_import_rejected() -> None:
    """Importing a non-Orcheo module is rejected."""
    with pytest.raises(WorkflowValidationError, match="must come from Orcheo"):
        _validate(
            """
            from langgraph.graph import StateGraph
            def orcheo_workflow():
                return StateGraph(None)
            """
        )


def test_star_import_rejected() -> None:
    """A star import from Orcheo is rejected."""
    with pytest.raises(WorkflowValidationError, match="star imports"):
        _validate(
            """
            from orcheo.nodes import *
            def orcheo_workflow():
                return None
            """
        )


def test_class_decorator_rejected() -> None:
    """A decorator on a CodeNode subclass is rejected."""
    with pytest.raises(WorkflowValidationError, match="decorators are not allowed"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            @staticmethod
            class X(CodeNode):
                async def run(self, state, config):
                    return {}
            def orcheo_workflow():
                return None
            """
        )


def test_metaclass_keyword_rejected() -> None:
    """A metaclass keyword on a class is rejected."""
    with pytest.raises(WorkflowValidationError, match="metaclass"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(CodeNode, metaclass=type):
                async def run(self, state, config):
                    return {}
            def orcheo_workflow():
                return None
            """
        )


def test_non_codenode_subclass_rejected() -> None:
    """A class not inheriting from CodeNode is rejected."""
    with pytest.raises(
        WorkflowValidationError, match="must inherit only from CodeNode"
    ):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(dict):
                async def run(self, state, config):
                    return {}
            def orcheo_workflow():
                return None
            """
        )


def test_extra_class_method_rejected() -> None:
    """A CodeNode subclass with a non-run method is rejected."""
    with pytest.raises(WorkflowValidationError, match="only define a 'run' method"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(CodeNode):
                def helper(self):
                    return 1
                async def run(self, state, config):
                    return {}
            def orcheo_workflow():
                return None
            """
        )


def test_default_factory_callable_field_rejected() -> None:
    """A non-literal (callable) class field default is rejected."""
    with pytest.raises(WorkflowValidationError, match="JSON literals"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(CodeNode):
                values = list()
                async def run(self, state, config):
                    return {}
            def orcheo_workflow():
                return None
            """
        )


def test_wrong_entrypoint_name_rejected() -> None:
    """An entrypoint not named ``orcheo_workflow`` is rejected."""
    with pytest.raises(
        WorkflowValidationError, match="must be named 'orcheo_workflow'"
    ):
        _validate(
            """
            from orcheo.graph import StateGraph
            def build():
                return StateGraph(None)
            """
        )


def test_missing_entrypoint_rejected() -> None:
    """A script with no entrypoint is rejected."""
    with pytest.raises(WorkflowValidationError, match="exactly one"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(CodeNode):
                async def run(self, state, config):
                    return {}
            """
        )


def test_multiple_entrypoints_rejected() -> None:
    """Two entrypoint functions are rejected."""
    with pytest.raises(WorkflowValidationError, match="exactly one"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            async def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            """
        )


def test_entrypoint_with_arguments_rejected() -> None:
    """An entrypoint taking arguments is rejected."""
    with pytest.raises(WorkflowValidationError, match="zero arguments"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow(config):
                return StateGraph(None)
            """
        )


def test_top_level_statement_rejected() -> None:
    """An arbitrary top-level statement is rejected."""
    with pytest.raises(WorkflowValidationError, match="module level"):
        _validate(
            """
            from orcheo.graph import StateGraph
            x = compute()
            def orcheo_workflow():
                return StateGraph(None)
            """
        )


def test_gadget_dunder_access_rejected() -> None:
    """A dunder gadget chain in the entrypoint is rejected."""
    with pytest.raises(WorkflowValidationError, match="private/dunder"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                graph.add_node("x", ().__class__)
                return graph
            """
        )


def test_lambda_node_rejected() -> None:
    """A lambda used as a node is rejected."""
    with pytest.raises(WorkflowValidationError, match="lambdas are not allowed"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                graph.add_node("x", lambda s: s)
                return graph
            """
        )


def test_comprehension_in_entrypoint_rejected() -> None:
    """A comprehension inside a construction call is rejected by the sweep."""
    with pytest.raises(WorkflowValidationError, match="comprehensions"):
        _validate(
            """
            from orcheo.graph import StateGraph
            from orcheo.nodes.logic import SetVariableNode
            def orcheo_workflow():
                graph = StateGraph(None)
                graph.add_node(
                    "x", SetVariableNode(name="x", values=[n for n in range(3)])
                )
                return graph
            """
        )


def test_subscript_in_entrypoint_rejected() -> None:
    """Dynamic subscript access in construction code is rejected."""
    with pytest.raises(WorkflowValidationError, match="subscript"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                graph.add_edge(START, registry[0])
                return graph
            """
        )


def test_disallowed_loop_rejected() -> None:
    """A ``for`` loop in the entrypoint is rejected."""
    with pytest.raises(WorkflowValidationError, match="not allowed"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                for i in range(3):
                    graph.add_edge(START, "x")
                return graph
            """
        )
