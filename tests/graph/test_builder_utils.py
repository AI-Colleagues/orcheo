"""Tests for low-level builder utilities such as edge normalisation."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
import pytest
from langgraph.graph import END, START
from orcheo.graph.conditional import make_condition
from orcheo.graph.normalization import normalise_edges, normalise_vertex
from orcheo.graph.parallel import add_parallel_branches
from tests.graph._builder_test_helpers import DummyGraph


def test_normalise_edges_validation() -> None:
    """Edge normalisation rejects malformed entries."""

    with pytest.raises(ValueError, match="Invalid edge entry"):
        normalise_edges([object()])  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Edge endpoints must be strings"):
        normalise_edges([("start", 1)])  # type: ignore[arg-type]


def test_normalise_edges_supports_mapping_entries() -> None:
    """Mapping-style edge definitions are normalised correctly."""

    result = normalise_edges([{"source": "A", "target": "B"}])
    assert result == [("A", "B")]


@pytest.mark.parametrize(
    ("config", "expected_message"),
    [
        ({"targets": ["A"]}, "source string"),
        ({"source": "START", "targets": "A"}, "list of targets"),
        (
            {"source": "START", "targets": ["A", 1]},
            "targets must be strings",
        ),
        ({"source": "START", "targets": []}, "targets must be strings"),
    ],
)
def test_add_parallel_branches_validation(
    config: Mapping[str, Any], expected_message: str
) -> None:
    """Parallel branch validation surfaces precise errors."""

    graph = DummyGraph()

    with pytest.raises(ValueError, match=expected_message):
        add_parallel_branches(graph, config)


def test_add_parallel_branches_with_join() -> None:
    """Parallel branches normalise endpoints and add join edges."""

    graph = DummyGraph()

    add_parallel_branches(
        graph,
        {"source": "START", "targets": ["A", "END"], "join": "END"},
    )

    assert graph.edges[:2] == [(START, "A"), (START, END)]
    assert graph.edges[2:] == [("A", END), (END, END)]


def test_add_parallel_branches_without_join() -> None:
    """Parallel branches may omit a join target."""

    graph = DummyGraph()

    add_parallel_branches(
        graph,
        {"source": "A", "targets": ["B", "C"]},
    )

    assert graph.edges == [("A", "B"), ("A", "C")]


def test_make_condition_falls_back_to_default_and_end() -> None:
    """The generated resolver handles nulls, defaults and missing paths."""

    mapping = {"true": "pos", "false": "neg", "value": "other"}
    condition = make_condition(
        "payload.result",
        mapping,
        default_target="fallback",
    )

    assert condition({"payload": {"result": True}}) == "pos"
    assert condition({"payload": {"result": False}}) == "neg"
    assert condition({"payload": {"result": "value"}}) == "other"
    assert condition({"payload": {"result": None}}) == "fallback"
    assert condition({"payload": {}}) == "fallback"
    assert condition({"payload": 7}) == "fallback"

    no_default = make_condition("payload.value", {}, default_target=None)
    with pytest.raises(ValueError, match="no 'default' target was configured"):
        no_default({"payload": {"value": 123}})


def test_make_condition_null_key_handling() -> None:
    """Test that null values are mapped to 'null' key in condition mapping."""

    mapping = {"null": "null_handler", "value": "value_handler"}
    condition = make_condition("data.field", mapping, default_target=None)

    assert condition({"data": {"field": None}}) == "null_handler"


def test_normalise_vertex() -> None:
    """Test vertex normalisation for START and END sentinels."""

    assert normalise_vertex("START") is START
    assert normalise_vertex("END") is END
    assert normalise_vertex("regular_node") == "regular_node"
