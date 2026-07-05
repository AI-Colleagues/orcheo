"""Tests for the frozen workflow IR Pydantic models (Task 1.1)."""

from __future__ import annotations
import pytest
from pydantic import ValidationError
from orcheo.graph.ir.models import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_STATE_REF,
    BuiltinNodeSpec,
    CodeNodeSpec,
    ConditionalEdgeSpec,
    EdgeSpec,
    GraphIR,
)


def _sample_ir() -> GraphIR:
    """Return an IR exercising every spec type for round-trip checks."""
    return GraphIR(
        entrypoint="triage",
        nodes=[
            BuiltinNodeSpec(
                id="triage",
                type="AgentNode",
                config={"ai_model": "claude-opus-4-8", "system_prompt": "Rate 0-10."},
            ),
            CodeNodeSpec(
                id="verdict",
                config={"threshold": 8},
                injected=["threshold"],
                body='return {"verdict": "pass"}',
            ),
        ],
        edges=[
            EdgeSpec(source="__start__", target="triage"),
            EdgeSpec(source="triage", target="verdict"),
        ],
        conditional_edges=[
            ConditionalEdgeSpec(
                source="verdict",
                path="node_results.verdict.verdict",
                mapping={"pass": "approve", "fail": "__end__"},
            )
        ],
    )


def test_graph_ir_defaults() -> None:
    """Unset schema_version and state_ref fall back to the fixed defaults."""
    ir = GraphIR(
        entrypoint="only", nodes=[BuiltinNodeSpec(id="only", type="DebugNode")]
    )

    assert ir.schema_version == CURRENT_SCHEMA_VERSION
    assert ir.state_ref == DEFAULT_STATE_REF
    assert ir.edges == []
    assert ir.conditional_edges == []


def test_graph_ir_json_round_trip() -> None:
    """An IR serialises to JSON and parses back to an equal model."""
    ir = _sample_ir()

    restored = GraphIR.model_validate_json(ir.model_dump_json())

    assert restored == ir


def test_graph_ir_dict_round_trip_preserves_discriminator() -> None:
    """Round-tripping via dict resolves the node-spec discriminator correctly."""
    ir = _sample_ir()

    restored = GraphIR.model_validate(ir.model_dump())

    assert isinstance(restored.nodes[0], BuiltinNodeSpec)
    assert isinstance(restored.nodes[1], CodeNodeSpec)
    assert restored.nodes[1].injected == ["threshold"]


def test_node_spec_discriminator_selects_code_spec() -> None:
    """A ``kind="code"`` mapping parses into a CodeNodeSpec."""
    ir = GraphIR.model_validate(
        {
            "entrypoint": "n",
            "nodes": [{"kind": "code", "id": "n", "body": "return {}", "injected": []}],
        }
    )

    assert isinstance(ir.nodes[0], CodeNodeSpec)


def test_spec_rejects_extra_fields() -> None:
    """Stray/misspelled keys are rejected so malformed specs fail fast."""
    with pytest.raises(ValidationError):
        BuiltinNodeSpec(id="x", type="DebugNode", confg={"oops": True})  # type: ignore[call-arg]


def test_spec_is_frozen() -> None:
    """IR specs are immutable once constructed."""
    spec = BuiltinNodeSpec(id="x", type="DebugNode")

    with pytest.raises(ValidationError):
        spec.id = "y"  # type: ignore[misc]
