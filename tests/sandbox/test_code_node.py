"""End-to-end tests for sandboxed CodeNode execution (Task 3.7)."""

from __future__ import annotations
import textwrap
import pytest
from orcheo.graph.ir import compile_workflow_to_ir
from orcheo.sandbox.code_node import (
    SandboxCodeNode,
    SandboxMetrics,
    build_sandboxed_state_graph,
    make_code_node_factory,
)
from orcheo.sandbox.exceptions import SandboxExecutionError
from orcheo.sandbox.runner import MicroPythonSandboxRunner


def _ir(source: str):
    """Compile a dedented workflow source into an IR."""
    return compile_workflow_to_ir(textwrap.dedent(source))


def _real_runner() -> MicroPythonSandboxRunner:
    """Return a real runner with enough headroom for loaded CI workers."""

    return MicroPythonSandboxRunner(wall_timeout_seconds=5.0)


TRANSFORM_WORKFLOW = """
    from orcheo.graph import StateGraph, START, END
    from orcheo.graph.state import State
    from orcheo.nodes.logic import SetVariableNode
    from orcheo.nodes import CodeNode

    class Doubler(CodeNode):
        factor: int = 2

        async def run(self, state, config):
            value = state["results"]["setter"]["value"]
            return {"results": {"doubled": value * self.factor}}

    async def orcheo_workflow() -> StateGraph:
        graph = StateGraph(State)
        graph.add_node("setter", SetVariableNode(name="setter", variables={"value": 21}))
        graph.add_node("doubler", Doubler(name="doubler", factor=3))
        graph.add_edge(START, "setter")
        graph.add_edge("setter", "doubler")
        graph.add_edge("doubler", END)
        return graph
"""


@pytest.mark.asyncio
async def test_end_to_end_transform_and_merge_back() -> None:
    """A CodeNode body runs in the sandbox and merges its update into state."""
    ir = _ir(TRANSFORM_WORKFLOW)
    metrics = SandboxMetrics()
    compiled = build_sandboxed_state_graph(
        ir, runner=_real_runner(), metrics=metrics
    ).compile()

    result = await compiled.ainvoke({"inputs": {}})

    # factor=3 (constructor kwarg) overrides the class default of 2.
    assert result["results"]["doubled"] == 63
    assert result["results"]["setter"] == {"value": 21}
    assert metrics.invocations == 1
    assert metrics.successes == 1


@pytest.mark.asyncio
async def test_merge_back_respects_results_reducer() -> None:
    """Updates from successive nodes merge via the ``results`` dict reducer."""
    ir = _ir(TRANSFORM_WORKFLOW)
    compiled = build_sandboxed_state_graph(ir, runner=_real_runner()).compile()

    result = await compiled.ainvoke({"inputs": {}})

    # Both the built-in setter result and the CodeNode result coexist.
    assert set(result["results"]) >= {"setter", "doubled"}


@pytest.mark.asyncio
async def test_state_template_in_injected_config_is_resolved() -> None:
    """``{{state}}`` templates in injected config resolve host-side before run."""
    ir = _ir(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.logic import SetVariableNode
        from orcheo.nodes import CodeNode

        class Echo(CodeNode):
            label: str = "x"

            async def run(self, state, config):
                return {"results": {"echo": self.label}}

        async def orcheo_workflow() -> StateGraph:
            graph = StateGraph(State)
            graph.add_node(
                "setter", SetVariableNode(name="setter", variables={"who": "world"})
            )
            graph.add_node(
                "echo", Echo(name="echo", label="{{results.setter.who}}")
            )
            graph.add_edge(START, "setter")
            graph.add_edge("setter", "echo")
            graph.add_edge("echo", END)
            return graph
        """
    )
    compiled = build_sandboxed_state_graph(ir, runner=_real_runner()).compile()

    result = await compiled.ainvoke({"inputs": {}})

    assert result["results"]["echo"] == "world"


@pytest.mark.asyncio
async def test_limit_breach_raises_node_attributed_error() -> None:
    """An infinite-loop body fails the run with a node-attributed limit error."""
    ir = _ir(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes import CodeNode

        class Spin(CodeNode):
            async def run(self, state, config):
                x = 0
                while True:
                    x = x + 1
                return {"results": {"x": x}}

        async def orcheo_workflow() -> StateGraph:
            graph = StateGraph(State)
            graph.add_node("spin", Spin(name="spin"))
            graph.add_edge(START, "spin")
            graph.add_edge("spin", END)
            return graph
        """
    )
    runner = MicroPythonSandboxRunner(fuel=1_000_000, wall_timeout_seconds=2.0)
    metrics = SandboxMetrics()
    compiled = build_sandboxed_state_graph(ir, runner=runner, metrics=metrics).compile()

    with pytest.raises(Exception) as exc_info:
        await compiled.ainvoke({"inputs": {}})

    assert "spin" in str(exc_info.value)
    assert metrics.limit_errors == 1


@pytest.mark.asyncio
async def test_body_exception_propagates_as_execution_error() -> None:
    """A raising body surfaces as a node-attributed execution error."""
    ir = _ir(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes import CodeNode

        class Boom(CodeNode):
            async def run(self, state, config):
                raise ValueError("nope")
                return {"results": {}}

        async def orcheo_workflow() -> StateGraph:
            graph = StateGraph(State)
            graph.add_node("boom", Boom(name="boom"))
            graph.add_edge(START, "boom")
            graph.add_edge("boom", END)
            return graph
        """
    )
    metrics = SandboxMetrics()
    compiled = build_sandboxed_state_graph(
        ir, runner=_real_runner(), metrics=metrics
    ).compile()

    with pytest.raises(SandboxExecutionError) as exc_info:
        await compiled.ainvoke({"inputs": {}})

    assert exc_info.value.error_type == "ValueError"
    assert metrics.execution_errors == 1


def test_factory_matches_builder_contract() -> None:
    """The factory produces a runnable node from a CodeNodeSpec."""
    from orcheo.graph.ir.models import CodeNodeSpec

    factory = make_code_node_factory()
    node = factory(CodeNodeSpec(id="c", body="return {}"))

    assert node.name == "c"
    assert callable(node)


class _RaisingRunner:
    """A stand-in runner whose ``run`` always raises a preset error."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def run(self, body: str, inputs: dict, *, node_id: str | None = None) -> dict:
        del body, inputs, node_id
        raise self._exc


def _sandbox_node(runner: object, metrics: SandboxMetrics) -> object:
    from orcheo.graph.ir.models import CodeNodeSpec

    return SandboxCodeNode(
        CodeNodeSpec(id="n", body="return {}"), runner, metrics=metrics
    )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_runner_output_error_is_counted_and_reraised() -> None:
    """A ``SandboxOutputError`` from the runner is counted and re-raised."""
    from orcheo.sandbox.exceptions import SandboxOutputError

    metrics = SandboxMetrics()
    node = _sandbox_node(
        _RaisingRunner(SandboxOutputError("bad", node_id="n")), metrics
    )

    with pytest.raises(SandboxOutputError):
        await node({"inputs": {}}, {"configurable": {}})

    assert metrics.output_errors == 1


@pytest.mark.asyncio
async def test_runner_generic_sandbox_error_is_counted_and_reraised() -> None:
    """A generic ``SandboxError`` from the runner is counted and re-raised."""
    from orcheo.sandbox.exceptions import SandboxError

    metrics = SandboxMetrics()
    node = _sandbox_node(_RaisingRunner(SandboxError("kaboom", node_id="n")), metrics)

    with pytest.raises(SandboxError):
        await node({"inputs": {}}, {"configurable": {}})

    assert metrics.execution_errors == 1


def test_interpret_outputs_rejects_non_mapping_update() -> None:
    """An ``update`` envelope that is not a mapping raises an output error."""
    from orcheo.sandbox.exceptions import SandboxOutputError

    metrics = SandboxMetrics()
    node = _sandbox_node(_RaisingRunner(RuntimeError("unused")), metrics)

    with pytest.raises(SandboxOutputError, match="must be a JSON object"):
        node._interpret_outputs({"update": "not-a-mapping"})

    assert metrics.output_errors == 1
