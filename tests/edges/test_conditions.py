import asyncio
from typing import cast
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.edges.branching import _coerce_branch_key
from orcheo.edges.conditions import (
    ComparisonOperator,
    Condition,
    _combine_condition_results,
    _contains,
    evaluate_condition,
)
from orcheo.graph.state import State
from orcheo.nodes.logic.utilities import DelayNode, _build_nested


def test_evaluate_condition_raises_for_unknown_operator() -> None:
    with pytest.raises(ValueError):
        evaluate_result = cast(ComparisonOperator, "__invalid__")
        evaluate_condition(
            left=1,
            right=2,
            operator=evaluate_result,
            case_sensitive=True,
        )


@pytest.mark.parametrize(
    ("left", "operator", "right", "expected"),
    [
        (10, "not_equals", 5, True),
        (10, "not_equals", 10, False),
        (10, "greater_than_or_equal", 10, True),
        (10, "greater_than_or_equal", 5, True),
        (5, "greater_than_or_equal", 10, False),
        (5, "less_than", 10, True),
        (10, "less_than", 5, False),
        (5, "less_than_or_equal", 10, True),
        (5, "less_than_or_equal", 5, True),
        (10, "less_than_or_equal", 5, False),
        (True, "is_truthy", None, True),
        ("value", "is_truthy", None, True),
        (0, "is_truthy", None, False),
        (False, "is_falsy", None, True),
        ("", "is_falsy", None, True),
        (1, "is_falsy", None, False),
    ],
)
def test_evaluate_condition_all_operators(
    left: object, operator: str, right: object, expected: bool
) -> None:
    result = evaluate_condition(
        left=left,
        operator=operator,
        right=right,
        case_sensitive=True,
    )
    assert result is expected


def test_contains_with_string_as_bytes() -> None:
    result = _contains("hello world", "world", expect=True)
    assert result is True

    result = _contains("hello world", "missing", expect=False)
    assert result is True


@pytest.mark.asyncio
async def test_delay_node_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    called_with: list[float] = []

    async def fake_sleep(duration: float) -> None:
        called_with.append(duration)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    state = State({"node_results": {}})
    node = DelayNode(name="pause", duration_seconds=0.5)

    result = await node(state, RunnableConfig())
    payload = result["node_results"]["pause"]

    assert called_with == [0.5]
    assert payload["duration_seconds"] == 0.5


def test_coerce_branch_key_prefers_candidate_and_generates_slug() -> None:
    assert _coerce_branch_key("  custom-branch  ", "fallback") == "custom-branch"
    assert _coerce_branch_key("", "Default Value") == "default_value"


def test_coerce_branch_key_strips_whitespace() -> None:
    assert _coerce_branch_key("  branch-1  ", "fallback") == "branch-1"


def test_coerce_branch_key_generates_slug_with_special_chars() -> None:
    assert _coerce_branch_key("", "My Branch!@#") == "my_branch"
    assert _coerce_branch_key(None, "Test-Case_123") == "test-case_123"


def test_combine_condition_results_handles_empty_input() -> None:
    aggregated, evaluations = _combine_condition_results(
        conditions=[],
        combinator="and",
    )

    assert aggregated is False
    assert evaluations == []


def test_combine_condition_results_with_or_combinator() -> None:
    aggregated, evaluations = _combine_condition_results(
        conditions=[
            Condition(left=1, operator="equals", right=2),
            Condition(left=3, operator="equals", right=3),
        ],
        combinator="or",
    )

    assert aggregated is True
    assert len(evaluations) == 2
    assert evaluations[0]["result"] is False
    assert evaluations[1]["result"] is True


def test_combine_condition_results_uses_default_left() -> None:
    aggregated, evaluations = _combine_condition_results(
        conditions=[
            Condition(operator="greater_than", right=5),
        ],
        combinator="and",
        default_left=10,
    )

    assert aggregated is True
    assert evaluations[0]["left"] == 10
    assert evaluations[0]["result"] is True


def test_build_nested_validates_paths() -> None:
    with pytest.raises(ValueError):
        _build_nested("", "value")

    with pytest.raises(ValueError):
        _build_nested("...", "value")


def test_build_nested_creates_single_level_dict() -> None:
    result = _build_nested("key", "value")
    assert result == {"key": "value"}


def test_build_nested_creates_nested_dict() -> None:
    result = _build_nested("level1.level2.level3", "deep_value")
    assert result == {"level1": {"level2": {"level3": "deep_value"}}}


def test_build_nested_handles_whitespace_in_path() -> None:
    result = _build_nested("  outer  .  inner  ", 42)
    assert result == {"outer": {"inner": 42}}
