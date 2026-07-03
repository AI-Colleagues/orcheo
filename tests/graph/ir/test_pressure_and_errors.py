"""Pressure tests and error-message quality checks (Tasks 5.1, 5.2).

Task 5.1 records the authoring changes real (unrestricted) workflows need before
they conform to the restricted grammar: source graph symbols from ``orcheo.graph``,
drop non-Orcheo imports, replace raw-function nodes with ``CodeNode`` subclasses,
use the fixed ``State`` schema, and name the entrypoint ``orcheo_workflow``. These
tests demonstrate a conformed workflow compiles and runs, and that each legacy
construct is rejected with an actionable, line-referenced message.
"""

from __future__ import annotations
import textwrap
import pytest
from orcheo.graph.ir.builder import MAX_GRAPH_DEPTH
from orcheo.graph.ir.exceptions import WorkflowValidationError
from orcheo.graph.ir.interpreter import compile_workflow_to_ir


def _compile(source: str):
    """Compile a dedented workflow source into an IR."""
    return compile_workflow_to_ir(textwrap.dedent(source))


# A representative workflow conformed to the restricted grammar: built-in nodes,
# a CodeNode, a conditional edge, and templated config.
CONFORMED = """
    from orcheo.graph import StateGraph, START, END
    from orcheo.graph.state import State
    from orcheo.nodes.logic import SetVariableNode
    from orcheo.nodes import CodeNode

    class Grade(CodeNode):
        threshold: int = 5

        async def run(self, state, config):
            score = state["results"]["score"]["value"]
            verdict = "pass" if score >= self.threshold else "fail"
            return {"verdict": verdict}

    async def orcheo_workflow() -> StateGraph:
        graph = StateGraph(State)
        graph.add_node("score", SetVariableNode(name="score", variables={"value": 7}))
        graph.add_node("grade", Grade(name="grade", threshold=5))
        graph.add_edge(START, "score")
        graph.add_edge("score", "grade")
        graph.add_conditional_edges(
            "grade",
            {
                "path": "results.grade.verdict",
                "mapping": {"pass": "score", "fail": END},
            },
        )
        return graph
    """


def test_conformed_representative_workflow_compiles() -> None:
    """A representative workflow conformed to the grammar compiles to IR."""
    ir = _compile(CONFORMED)

    assert ir.entrypoint == "score"
    assert {n.id for n in ir.nodes} == {"score", "grade"}
    assert ir.conditional_edges[0].mapping == {"pass": "score", "fail": "__end__"}


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        # Legacy: langgraph import instead of the orcheo re-export.
        (
            "from langgraph.graph import StateGraph\n"
            "def orcheo_workflow():\n    graph = StateGraph(dict)\n    return graph\n",
            "must come from Orcheo",
        ),
        # Legacy: typing import for annotations.
        (
            "from typing import Any\n"
            "from orcheo.graph import StateGraph\n"
            "def orcheo_workflow():\n    graph = StateGraph(None)\n    return graph\n",
            "must come from Orcheo",
        ),
        # Legacy: raw function used as a node — the helper def cannot exist at
        # module level (only ``orcheo_workflow`` and CodeNode classes may).
        (
            "from orcheo.graph import StateGraph, START\n"
            "def greet(state):\n    return state\n"
            "def orcheo_workflow():\n"
            "    graph = StateGraph(None)\n"
            "    graph.add_node('g', greet)\n"
            "    graph.add_edge(START, 'g')\n"
            "    return graph\n",
            "named 'orcheo_workflow'",
        ),
        # Legacy: entrypoint not named orcheo_workflow.
        (
            "from orcheo.graph import StateGraph\n"
            "def build_graph():\n    graph = StateGraph(None)\n    return graph\n",
            "must be named 'orcheo_workflow'",
        ),
        # Legacy: __main__ guard / top-level statement.
        (
            "from orcheo.graph import StateGraph\n"
            "def orcheo_workflow():\n    graph = StateGraph(None)\n    return graph\n"
            "x = orcheo_workflow()\n",
            "module level",
        ),
    ],
)
def test_legacy_constructs_are_rejected(source: str, needle: str) -> None:
    """Each common legacy construct is rejected with a clear message."""
    with pytest.raises(WorkflowValidationError, match=needle):
        compile_workflow_to_ir(source)


@pytest.mark.parametrize(
    "source",
    [
        # Grammar failure (decorator on entrypoint) — line 3.
        "from orcheo.graph import StateGraph\n\n@cache\n"
        "def orcheo_workflow():\n    return StateGraph(None)\n",
    ],
)
def test_grammar_errors_carry_line_numbers(source: str) -> None:
    """Grammar violations surface the offending source line."""
    with pytest.raises(WorkflowValidationError) as exc_info:
        compile_workflow_to_ir(source)

    assert exc_info.value.lineno is not None
    assert str(exc_info.value).startswith(f"line {exc_info.value.lineno}:")


def test_config_value_error_carries_line_number() -> None:
    """A disallowed CodeNode credential placeholder reports its line."""
    source = textwrap.dedent(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes import CodeNode

        class Secret(CodeNode):
            async def run(self, state, config):
                return {"x": self.token}

        async def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node("s", Secret(name="s", token="[[api_key]]"))
            graph.add_edge(START, "s")
            graph.add_edge("s", END)
            return graph
        """
    )
    with pytest.raises(WorkflowValidationError) as exc_info:
        compile_workflow_to_ir(source)

    assert exc_info.value.lineno is not None
    assert "CodeNode config" in exc_info.value.raw_message


def test_builtin_allowlist_error_carries_line_number() -> None:
    """An unsupported builtin in a CodeNode body reports its line."""
    source = textwrap.dedent(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes import CodeNode

        class Bad(CodeNode):
            async def run(self, state, config):
                return {"x": eval("1+1")}

        async def orcheo_workflow():
            graph = StateGraph(State)
            graph.add_node("b", Bad(name="b"))
            graph.add_edge(START, "b")
            graph.add_edge("b", END)
            return graph
        """
    )
    with pytest.raises(WorkflowValidationError) as exc_info:
        compile_workflow_to_ir(source)

    assert exc_info.value.lineno is not None
    assert "builtin 'eval'" in exc_info.value.raw_message


def _nested_graphs_source(depth: int) -> str:
    """Build a workflow nesting ``depth`` subgraphs, one inside the next."""
    lines = [
        "from orcheo.graph import StateGraph, START, END",
        "from orcheo.graph.state import State",
        "from orcheo.nodes.logic import SetVariableNode",
        "",
        "def orcheo_workflow():",
        "    g0 = StateGraph(State)",
        "    g0.add_node('leaf', SetVariableNode(name='leaf', variables={'x': 1}))",
        "    g0.add_edge(START, 'leaf')",
        "    g0.add_edge('leaf', END)",
    ]
    for level in range(1, depth + 1):
        prev = level - 1
        lines += [
            f"    g{level} = StateGraph(State)",
            f"    g{level}.add_node('n{level}', g{prev}.compile())",
            f"    g{level}.add_edge(START, 'n{level}')",
            f"    g{level}.add_edge('n{level}', END)",
        ]
    lines.append(f"    return g{depth}")
    return "\n".join(lines)


def test_deeply_nested_subgraphs_rejected_at_compile() -> None:
    """Acyclic but excessively nested subgraphs are rejected, not stack-overflowed."""
    source = _nested_graphs_source(MAX_GRAPH_DEPTH + 2)

    with pytest.raises(WorkflowValidationError, match="nesting|depth exceeds"):
        compile_workflow_to_ir(source)


def test_moderately_nested_subgraphs_compile() -> None:
    """Nesting within the depth limit still compiles successfully."""
    ir = compile_workflow_to_ir(_nested_graphs_source(3))

    assert ir.entrypoint == "n3"
