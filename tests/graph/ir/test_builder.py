"""Tests for IR re-validation and the trusted graph rebuilder (Tasks 1.3-1.5)."""

from __future__ import annotations
import pytest
from orcheo.graph.ir.builder import build_state_graph_from_ir, validate_ir
from orcheo.graph.ir.exceptions import IRValidationError
from orcheo.graph.ir.models import (
    BuiltinNodeSpec,
    CodeNodeSpec,
    ConditionalEdgeSpec,
    EdgeSpec,
    GraphIR,
)


def _debug_ir() -> GraphIR:
    """Two built-in DebugNodes wired START -> first -> second -> END."""
    return GraphIR(
        entrypoint="first",
        nodes=[
            BuiltinNodeSpec(id="first", type="DebugNode", config={"message": "hello"}),
            BuiltinNodeSpec(id="second", type="DebugNode", config={"message": "world"}),
        ],
        edges=[
            EdgeSpec(source="__start__", target="first"),
            EdgeSpec(source="first", target="second"),
            EdgeSpec(source="second", target="__end__"),
        ],
    )


@pytest.mark.asyncio
async def test_builtin_ir_builds_and_runs_without_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A built-in-node IR builds and runs without re-executing any script.

    The script loader is patched to fail loudly: if the rebuilder ever fell back
    to executing a ``workflow.py`` the run would raise instead of producing the
    expected node outputs.
    """
    import orcheo.graph.ingestion.loader as loader

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("load_graph_from_script must not run for an IR rebuild")

    monkeypatch.setattr(loader, "load_graph_from_script", _boom)

    graph = build_state_graph_from_ir(_debug_ir())
    compiled = graph.compile()

    result = await compiled.ainvoke({"inputs": {}})

    assert result["results"]["first"]["message"] == "hello"
    assert result["results"]["second"]["message"] == "world"


@pytest.mark.asyncio
async def test_ir_accepts_mapping_input() -> None:
    """The rebuilder coerces and validates a plain mapping IR."""
    graph = build_state_graph_from_ir(_debug_ir().model_dump())
    compiled = graph.compile()

    result = await compiled.ainvoke({"inputs": {}})

    assert result["results"]["first"]["message"] == "hello"


@pytest.mark.asyncio
async def test_conditional_edge_routes_on_state_path() -> None:
    """A declarative conditional edge routes on a dotted state path."""
    ir = GraphIR(
        entrypoint="first",
        nodes=[
            BuiltinNodeSpec(id="first", type="DebugNode", config={"message": "go"}),
            BuiltinNodeSpec(id="second", type="DebugNode", config={"message": "end"}),
        ],
        edges=[EdgeSpec(source="first", target="__end__")],
        conditional_edges=[
            ConditionalEdgeSpec(
                source="first",
                path="results.first.found",
                mapping={"false": "second", "true": "__end__"},
            )
        ],
    )

    compiled = build_state_graph_from_ir(ir).compile()
    result = await compiled.ainvoke({"inputs": {}})

    # DebugNode reports found=False (no tap_path), so we route to "second".
    assert result["results"]["second"]["message"] == "end"


def test_unknown_node_type_is_rejected() -> None:
    """An unregistered built-in type fails validation with a clear message."""
    ir = GraphIR(
        entrypoint="x",
        nodes=[BuiltinNodeSpec(id="x", type="NotARealNode")],
    )

    with pytest.raises(IRValidationError, match="Unknown built-in node type"):
        validate_ir(ir)


def test_duplicate_node_ids_are_rejected() -> None:
    """Duplicate node ids are rejected."""
    ir = GraphIR(
        entrypoint="dup",
        nodes=[
            BuiltinNodeSpec(id="dup", type="DebugNode"),
            BuiltinNodeSpec(id="dup", type="DebugNode"),
        ],
    )

    with pytest.raises(IRValidationError, match="Duplicate node id"):
        validate_ir(ir)


def test_dangling_edge_target_is_rejected() -> None:
    """An edge pointing at an unknown node is rejected."""
    ir = GraphIR(
        entrypoint="a",
        nodes=[BuiltinNodeSpec(id="a", type="DebugNode")],
        edges=[EdgeSpec(source="a", target="ghost")],
    )

    with pytest.raises(IRValidationError, match="unknown node"):
        validate_ir(ir)


def test_unknown_entrypoint_is_rejected() -> None:
    """An entrypoint that names no node is rejected."""
    ir = GraphIR(
        entrypoint="missing",
        nodes=[BuiltinNodeSpec(id="a", type="DebugNode")],
    )

    with pytest.raises(IRValidationError, match="entrypoint"):
        validate_ir(ir)


def test_unsupported_schema_version_is_rejected() -> None:
    """An IR carrying a future schema version is rejected."""
    ir = GraphIR(
        schema_version=999,
        entrypoint="a",
        nodes=[BuiltinNodeSpec(id="a", type="DebugNode")],
    )

    with pytest.raises(IRValidationError, match="schema_version"):
        validate_ir(ir)


def test_malformed_mapping_is_rejected() -> None:
    """A mapping missing required IR fields raises a clear validation error."""
    with pytest.raises(IRValidationError, match="Malformed workflow IR"):
        validate_ir({"nodes": [{"kind": "builtin", "id": "a"}]})


def test_conditional_edge_unknown_target_is_rejected() -> None:
    """A conditional-edge mapping target that names no node is rejected."""
    ir = GraphIR(
        entrypoint="a",
        nodes=[BuiltinNodeSpec(id="a", type="DebugNode")],
        conditional_edges=[
            ConditionalEdgeSpec(source="a", path="x", mapping={"v": "ghost"})
        ],
    )

    with pytest.raises(IRValidationError, match="unknown target"):
        validate_ir(ir)


def test_code_node_without_factory_is_rejected() -> None:
    """Building an IR with a CodeNode but no sandbox factory fails clearly."""
    ir = GraphIR(
        entrypoint="c",
        nodes=[CodeNodeSpec(id="c", body="return {}")],
    )

    with pytest.raises(IRValidationError, match="sandbox runner"):
        build_state_graph_from_ir(ir)


def test_code_node_factory_is_invoked() -> None:
    """A supplied factory is used to build the runnable for a CodeNodeSpec."""
    from orcheo.nodes.base import TaskNode

    class _Stub(TaskNode):
        async def run(self, state: object, config: object) -> dict[str, object]:
            del state, config
            return {}

    seen: list[str] = []

    def factory(spec: CodeNodeSpec) -> object:
        seen.append(spec.id)
        return _Stub(name=spec.id)

    ir = GraphIR(
        entrypoint="c",
        nodes=[CodeNodeSpec(id="c", body="return {}")],
        edges=[EdgeSpec(source="c", target="__end__")],
    )

    build_state_graph_from_ir(ir, code_node_factory=factory)

    assert seen == ["c"]
