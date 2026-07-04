"""Tests for IR re-validation and the trusted graph rebuilder (Tasks 1.3-1.5)."""

from __future__ import annotations
import pytest
from orcheo.graph.ir.builder import (
    MAX_GRAPH_DEPTH,
    _build_workflow_tool,
    build_state_graph_from_ir,
    validate_ir,
)
from orcheo.graph.ir.exceptions import IRValidationError
from orcheo.graph.ir.models import (
    IR_CONFIG_KIND_KEY,
    PYDANTIC_MODEL_CONFIG_KIND,
    WORKFLOW_TOOL_CONFIG_KIND,
    BuiltinNodeSpec,
    CodeNodeSpec,
    ConditionalEdgeSpec,
    EdgeSpec,
    GraphIR,
    SubgraphNodeSpec,
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
async def test_subgraph_ir_builds_and_runs_nested_graph() -> None:
    """A subgraph node is rebuilt recursively and executes inline."""
    ir = GraphIR(
        entrypoint="branch",
        nodes=[
            SubgraphNodeSpec(
                id="branch",
                graph=GraphIR(
                    entrypoint="inner",
                    nodes=[
                        BuiltinNodeSpec(
                            id="inner",
                            type="SetVariableNode",
                            config={"variables": {"value": 42}},
                        )
                    ],
                    edges=[
                        EdgeSpec(source="__start__", target="inner"),
                        EdgeSpec(source="inner", target="__end__"),
                    ],
                ),
            )
        ],
        edges=[
            EdgeSpec(source="__start__", target="branch"),
            EdgeSpec(source="branch", target="__end__"),
        ],
    )

    compiled = build_state_graph_from_ir(ir).compile()
    result = await compiled.ainvoke({"inputs": {}})

    assert result["results"]["inner"]["value"] == 42


def test_agent_workflow_tool_ir_materialises_workflow_tool() -> None:
    """Workflow-tool markers in built-in config rebuild to runtime WorkflowTool objects."""
    from orcheo.graph.ir.models import IR_CONFIG_KIND_KEY, WORKFLOW_TOOL_CONFIG_KIND

    ir = GraphIR(
        entrypoint="agent",
        nodes=[
            BuiltinNodeSpec(
                id="agent",
                type="AgentNode",
                config={
                    "ai_model": "gpt-4o-mini",
                    "workflow_tools": [
                        {
                            IR_CONFIG_KIND_KEY: WORKFLOW_TOOL_CONFIG_KIND,
                            "name": "lookup",
                            "description": "Look up context",
                            "graph": GraphIR(
                                entrypoint="inner",
                                nodes=[
                                    BuiltinNodeSpec(
                                        id="inner",
                                        type="SetVariableNode",
                                        config={"variables": {"value": 42}},
                                    )
                                ],
                                edges=[
                                    EdgeSpec(source="__start__", target="inner"),
                                    EdgeSpec(source="inner", target="__end__"),
                                ],
                            ).model_dump(),
                        }
                    ],
                },
            )
        ],
        edges=[
            EdgeSpec(source="__start__", target="agent"),
            EdgeSpec(source="agent", target="__end__"),
        ],
    )

    graph = build_state_graph_from_ir(ir)
    agent = graph.nodes["agent"].runnable.afunc

    assert len(agent.workflow_tools) == 1
    assert agent.workflow_tools[0].name == "lookup"
    assert agent.workflow_tools[0].graph.nodes.keys() == {"inner"}


def test_pydantic_model_ir_materialises_to_runtime_class() -> None:
    """Pydantic model markers rebuild to trusted Orcheo model classes."""
    from orcheo.nodes.qualitative import QuoteSelectionResponse

    ir = GraphIR(
        entrypoint="finalize",
        nodes=[
            BuiltinNodeSpec(
                id="finalize",
                type="LLMStageFinalizeNode",
                config={
                    "stage": "quote_selector",
                    "response_schema": {
                        IR_CONFIG_KIND_KEY: PYDANTIC_MODEL_CONFIG_KIND,
                        "module": "orcheo.nodes.qualitative",
                        "name": "QuoteSelectionResponse",
                    },
                },
            )
        ],
        edges=[
            EdgeSpec(source="__start__", target="finalize"),
            EdgeSpec(source="finalize", target="__end__"),
        ],
    )

    graph = build_state_graph_from_ir(ir)
    node = graph.nodes["finalize"].runnable.afunc

    assert node.response_schema is QuoteSelectionResponse


def test_pydantic_model_ir_rejects_non_orcheo_module() -> None:
    """Tampered Pydantic model markers cannot import outside Orcheo modules."""
    ir = GraphIR(
        entrypoint="first",
        nodes=[
            BuiltinNodeSpec(
                id="first",
                type="DebugNode",
                config={
                    "message": {
                        IR_CONFIG_KIND_KEY: PYDANTIC_MODEL_CONFIG_KIND,
                        "module": "pydantic",
                        "name": "BaseModel",
                    }
                },
            )
        ],
        edges=[EdgeSpec(source="__start__", target="first")],
    )

    with pytest.raises(IRValidationError, match="non-Orcheo module"):
        build_state_graph_from_ir(ir)


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


def _leaf_ir() -> GraphIR:
    """A trivial single-node graph used as a nesting/subgraph leaf."""
    return GraphIR(
        entrypoint="leaf",
        nodes=[BuiltinNodeSpec(id="leaf", type="DebugNode")],
    )


def test_excessive_subgraph_nesting_is_rejected() -> None:
    """Subgraph nesting beyond the depth limit is rejected, not stack-overflowed."""
    ir = _leaf_ir()
    for level in range(MAX_GRAPH_DEPTH + 2):
        ir = GraphIR(
            entrypoint=f"n{level}",
            nodes=[SubgraphNodeSpec(id=f"n{level}", graph=ir)],
        )

    with pytest.raises(IRValidationError, match="nesting depth exceeds"):
        validate_ir(ir)


def test_empty_node_id_is_rejected() -> None:
    """A blank node id is rejected."""
    ir = GraphIR(entrypoint="a", nodes=[BuiltinNodeSpec(id="   ", type="DebugNode")])

    with pytest.raises(IRValidationError, match="non-empty string"):
        validate_ir(ir)


def test_sentinel_node_id_is_rejected() -> None:
    """A node id colliding with a reserved sentinel is rejected."""
    ir = GraphIR(
        entrypoint="__start__",
        nodes=[BuiltinNodeSpec(id="__start__", type="DebugNode")],
    )

    with pytest.raises(IRValidationError, match="reserved sentinel"):
        validate_ir(ir)


def test_empty_entrypoint_is_rejected() -> None:
    """A blank entrypoint is rejected."""
    ir = GraphIR(entrypoint="", nodes=[BuiltinNodeSpec(id="a", type="DebugNode")])

    with pytest.raises(IRValidationError, match="non-empty node id"):
        validate_ir(ir)


def test_dangling_edge_source_is_rejected() -> None:
    """An edge whose source names no node is rejected."""
    ir = GraphIR(
        entrypoint="a",
        nodes=[BuiltinNodeSpec(id="a", type="DebugNode")],
        edges=[EdgeSpec(source="ghost", target="a")],
    )

    with pytest.raises(IRValidationError, match="edge source"):
        validate_ir(ir)


def test_conditional_edge_unknown_source_is_rejected() -> None:
    """A conditional edge whose source names no node is rejected."""
    ir = GraphIR(
        entrypoint="a",
        nodes=[BuiltinNodeSpec(id="a", type="DebugNode")],
        conditional_edges=[
            ConditionalEdgeSpec(source="ghost", path="p", mapping={"v": "a"})
        ],
    )

    with pytest.raises(IRValidationError, match="conditional edge source"):
        validate_ir(ir)


def test_conditional_edge_empty_mapping_is_rejected() -> None:
    """A conditional edge with an empty mapping is rejected."""
    ir = GraphIR(
        entrypoint="a",
        nodes=[BuiltinNodeSpec(id="a", type="DebugNode")],
        conditional_edges=[ConditionalEdgeSpec(source="a", path="p", mapping={})],
    )

    with pytest.raises(IRValidationError, match="non-empty mapping"):
        validate_ir(ir)


def test_conditional_edge_unknown_default_is_rejected() -> None:
    """A conditional edge with an unknown default target is rejected."""
    ir = GraphIR(
        entrypoint="a",
        nodes=[BuiltinNodeSpec(id="a", type="DebugNode")],
        conditional_edges=[
            ConditionalEdgeSpec(
                source="a", path="p", mapping={"v": "a"}, default="ghost"
            )
        ],
    )

    with pytest.raises(IRValidationError, match="unknown default target"):
        validate_ir(ir)


def test_workflow_tool_config_with_bad_graph_is_rejected() -> None:
    """A workflow-tool marker carrying a non-mapping graph fails validation."""
    ir = GraphIR(
        entrypoint="a",
        nodes=[
            BuiltinNodeSpec(
                id="a",
                type="AgentNode",
                config={
                    "workflow_tools": [
                        {
                            IR_CONFIG_KIND_KEY: WORKFLOW_TOOL_CONFIG_KIND,
                            "name": "t",
                            "description": "d",
                            "graph": "not-a-graph",
                        }
                    ]
                },
            )
        ],
    )

    with pytest.raises(
        IRValidationError, match="workflow tool requires a nested graph"
    ):
        validate_ir(ir)


def test_builtin_node_construction_failure_is_wrapped() -> None:
    """A built-in node whose config fails construction surfaces a clear error."""
    ir = GraphIR(
        entrypoint="a",
        nodes=[
            BuiltinNodeSpec(id="a", type="SetVariableNode", config={"variables": 123})
        ],
    )

    with pytest.raises(IRValidationError, match="Failed to construct node"):
        build_state_graph_from_ir(ir)


def test_build_workflow_tool_rejects_non_string_name() -> None:
    """A workflow-tool marker with a non-string name is rejected during build."""
    with pytest.raises(IRValidationError, match="string 'name' and 'description'"):
        _build_workflow_tool(
            {"name": 123, "description": "d", "graph": _leaf_ir().model_dump()},
            code_node_factory=None,
        )


def test_build_workflow_tool_rejects_non_mapping_graph() -> None:
    """A workflow-tool marker with a non-mapping graph is rejected during build."""
    with pytest.raises(IRValidationError, match="nested graph IR mapping"):
        _build_workflow_tool(
            {"name": "t", "description": "d", "graph": "nope"},
            code_node_factory=None,
        )


def test_workflow_tool_output_path_is_passed_through() -> None:
    """A workflow-tool ``output_path`` is forwarded to the runtime WorkflowTool."""
    nested = GraphIR(
        entrypoint="inner",
        nodes=[
            BuiltinNodeSpec(
                id="inner",
                type="SetVariableNode",
                config={"variables": {"value": 1}},
            )
        ],
        edges=[
            EdgeSpec(source="__start__", target="inner"),
            EdgeSpec(source="inner", target="__end__"),
        ],
    )
    ir = GraphIR(
        entrypoint="agent",
        nodes=[
            BuiltinNodeSpec(
                id="agent",
                type="AgentNode",
                config={
                    "ai_model": "gpt-4o-mini",
                    "workflow_tools": [
                        {
                            IR_CONFIG_KIND_KEY: WORKFLOW_TOOL_CONFIG_KIND,
                            "name": "lookup",
                            "description": "Look up context",
                            "graph": nested.model_dump(),
                            "output_path": "results.lookup",
                            "return_direct": True,
                        }
                    ],
                },
            )
        ],
        edges=[
            EdgeSpec(source="__start__", target="agent"),
            EdgeSpec(source="agent", target="__end__"),
        ],
    )

    graph = build_state_graph_from_ir(ir)
    agent = graph.nodes["agent"].runnable.afunc

    assert agent.workflow_tools[0].output_path == "results.lookup"
    assert agent.workflow_tools[0].return_direct is True
