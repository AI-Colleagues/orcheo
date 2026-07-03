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
        return {"v": self.threshold}


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


def test_relative_import_rejected() -> None:
    """A relative import is rejected."""
    with pytest.raises(WorkflowValidationError, match="relative imports"):
        _validate(
            """
            from . import helpers
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            """
        )


def test_plain_non_orcheo_import_rejected() -> None:
    """A plain ``import os`` (non-Orcheo) is rejected."""
    with pytest.raises(WorkflowValidationError, match="must come from Orcheo"):
        _validate(
            """
            import os
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            """
        )


def test_plain_orcheo_imports_accepted() -> None:
    """A multi-name plain ``import`` of Orcheo submodules validates."""
    _validate(
        """
        import orcheo.nodes, orcheo.graph
        def orcheo_workflow():
            graph = StateGraph(None)
            return graph
        """
    )


def test_entrypoint_decorator_rejected() -> None:
    """A decorator on the ``orcheo_workflow`` entrypoint is rejected."""
    with pytest.raises(WorkflowValidationError, match="decorators are not allowed"):
        _validate(
            """
            from orcheo.graph import StateGraph
            @cache
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            """
        )


def test_class_with_disallowed_statement_rejected() -> None:
    """A class body statement that is neither a field nor a method is rejected."""
    with pytest.raises(WorkflowValidationError, match="config fields and a 'run'"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(CodeNode):
                pass
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            """
        )


def test_class_without_run_method_rejected() -> None:
    """A CodeNode subclass with no ``run`` method is rejected."""
    with pytest.raises(WorkflowValidationError, match="must define a 'run' method"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(CodeNode):
                threshold: int = 5
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            """
        )


def test_run_method_decorator_rejected() -> None:
    """A decorator on the ``run`` method is rejected."""
    with pytest.raises(WorkflowValidationError, match="decorators are not allowed"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(CodeNode):
                @property
                def run(self, state, config):
                    return {}
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            """
        )


def test_class_field_multiple_targets_rejected() -> None:
    """A chained class-field assignment (``a = b = ...``) is rejected."""
    with pytest.raises(WorkflowValidationError, match="simple assignments"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(CodeNode):
                a = b = 5
                def run(self, state, config):
                    return {}
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            """
        )


def test_class_field_attribute_target_rejected() -> None:
    """An annotated class field whose target is not a bare name is rejected."""
    with pytest.raises(WorkflowValidationError, match="simple assignments"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(CodeNode):
                obj.attr: int = 5
                def run(self, state, config):
                    return {}
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            """
        )


def test_class_underscore_field_rejected() -> None:
    """A class field whose name starts with ``_`` is rejected."""
    with pytest.raises(WorkflowValidationError, match="may not start with '_'"):
        _validate(
            """
            from orcheo.nodes import CodeNode
            class X(CodeNode):
                _secret: int = 5
                def run(self, state, config):
                    return {}
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph
            """
        )


def test_class_annotation_only_field_is_accepted() -> None:
    """An annotation-only class field (no default value) validates."""
    _validate(
        """
        from orcheo.nodes import CodeNode
        class X(CodeNode):
            threshold: int
            def run(self, state, config):
                return {}
        def orcheo_workflow():
            graph = StateGraph(None)
            return graph
        """
    )


def test_entrypoint_multiple_target_assignment_rejected() -> None:
    """A multi-target assignment in the entrypoint is rejected."""
    with pytest.raises(WorkflowValidationError, match="single-name assignments"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                a = b = StateGraph(None)
                return a
            """
        )


def test_entrypoint_non_call_assignment_rejected() -> None:
    """An entrypoint assignment whose value is not a constructor call is rejected."""
    with pytest.raises(WorkflowValidationError, match="construct a graph or node"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = 5
                return graph
            """
        )


def test_entrypoint_attribute_constructor_assignment_rejected() -> None:
    """An assignment calling an attribute (not a bare class) is rejected."""
    with pytest.raises(
        WorkflowValidationError, match="call a graph or node class directly"
    ):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = mod.StateGraph(None)
                return graph
            """
        )


def test_entrypoint_bare_call_statement_rejected() -> None:
    """A bare function-call statement (not ``graph.method(...)``) is rejected."""
    with pytest.raises(WorkflowValidationError, match="graph-assembly method calls"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                helper()
                return graph
            """
        )


def test_entrypoint_unknown_graph_method_rejected() -> None:
    """A graph method outside the allowed assembly set is rejected."""
    with pytest.raises(
        WorkflowValidationError, match="not an allowed graph-assembly method"
    ):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                graph.frobnicate()
                return graph
            """
        )


def test_entrypoint_bare_return_rejected() -> None:
    """An entrypoint with a value-less ``return`` is rejected."""
    with pytest.raises(
        WorkflowValidationError, match="must return the assembled graph"
    ):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                return
            """
        )


def test_entrypoint_returning_compiled_graph_is_accepted() -> None:
    """Returning ``graph.compile()`` from the entrypoint validates."""
    _validate(
        """
        from orcheo.graph import StateGraph
        def orcheo_workflow():
            graph = StateGraph(None)
            return graph.compile()
        """
    )


def test_entrypoint_invalid_return_expression_rejected() -> None:
    """Returning an arbitrary call (not the graph or compile()) is rejected."""
    with pytest.raises(WorkflowValidationError, match="must return the graph"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                return graph.run()
            """
        )


def test_entrypoint_starred_argument_rejected() -> None:
    """A starred / unpacking argument in construction code is rejected."""
    with pytest.raises(WorkflowValidationError, match="starred / unpacking"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(*args)
                return graph
            """
        )


def test_entrypoint_await_in_construction_rejected() -> None:
    """An ``await`` inside construction code is rejected by the sweep."""
    with pytest.raises(WorkflowValidationError, match="await / yield"):
        _validate(
            """
            from orcheo.graph import StateGraph
            async def orcheo_workflow():
                graph = StateGraph(None)
                graph.add_node(await thing())
                return graph
            """
        )


def test_entrypoint_underscore_name_rejected() -> None:
    """A private/underscore name used in construction code is rejected."""
    with pytest.raises(WorkflowValidationError, match="private/underscore name"):
        _validate(
            """
            from orcheo.graph import StateGraph
            def orcheo_workflow():
                graph = StateGraph(None)
                graph.add_node("x", _secret)
                return graph
            """
        )
