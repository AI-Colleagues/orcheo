"""Tests for the config-value vocabulary validator (Task 2.5)."""

from __future__ import annotations
import ast
import pytest
from orcheo.graph.ir.config_values import (
    contains_credential_placeholder,
    literal_from_ast,
    validate_config_value,
)
from orcheo.graph.ir.exceptions import WorkflowValidationError


def _expr(code: str) -> ast.expr:
    """Parse ``code`` into a single expression node."""
    return ast.parse(code, mode="eval").body


@pytest.mark.parametrize(
    "code",
    [
        '"plain string"',
        "42",
        "-3.5",
        "True",
        "None",
        '["a", 1, {"k": "v"}]',
        '{"nested": {"deep": [1, 2, 3]}}',
        '"hello {{state.node_results.x}} world"',
    ],
)
def test_accepts_json_literals_and_templates(code: str) -> None:
    """JSON literals and ``{{state}}`` templates validate in any layer."""
    validate_config_value(_expr(code), allow_credentials=False, where="field 'x'")


def test_credential_allowed_in_builtin_config() -> None:
    """``[[credential]]`` is accepted when credentials are allowed (built-in)."""
    validate_config_value(
        _expr('"[[my_token]]"'), allow_credentials=True, where="node 'x'"
    )


def test_credential_rejected_in_code_node_config() -> None:
    """``[[credential]]`` is rejected when credentials are not allowed."""
    with pytest.raises(WorkflowValidationError, match="not allowed in CodeNode config"):
        validate_config_value(
            _expr('"[[my_token]]"'), allow_credentials=False, where="field 'x'"
        )


def test_embedded_credential_is_detected() -> None:
    """A credential placeholder embedded in a longer string is detected."""
    assert contains_credential_placeholder("Bearer [[token]] suffix")
    with pytest.raises(WorkflowValidationError):
        validate_config_value(
            _expr('"Bearer [[token]]"'), allow_credentials=False, where="field 'x'"
        )


@pytest.mark.parametrize(
    "code",
    [
        "some_name",  # name reference
        "func()",  # call
        "1 + 2",  # binop
        "[x for x in range(3)]",  # comprehension
        "lambda: 1",  # lambda
        "{1, 2, 3}",  # set literal (not JSON)
        "-some_name",  # unary minus on a non-constant operand
        "+other",  # unary plus on a non-constant operand
    ],
)
def test_rejects_non_literal_values(code: str) -> None:
    """Names, calls, arithmetic, comprehensions, lambdas, and sets are rejected."""
    with pytest.raises(WorkflowValidationError, match="JSON literals"):
        validate_config_value(_expr(code), allow_credentials=True, where="node 'x'")


def test_rejects_non_json_constant_literal() -> None:
    """A constant of an unsupported type (e.g. bytes) is rejected."""
    with pytest.raises(WorkflowValidationError, match="unsupported config literal"):
        validate_config_value(_expr('b"raw"'), allow_credentials=True, where="node 'x'")


def test_rejects_dict_unpacking() -> None:
    """``{**other}`` dict unpacking is rejected in config values."""
    with pytest.raises(WorkflowValidationError, match="dict unpacking is not allowed"):
        validate_config_value(
            _expr("{**other}"), allow_credentials=True, where="node 'x'"
        )


def test_rejects_non_string_dict_keys() -> None:
    """Non-string dict keys are rejected for JSON coercibility."""
    with pytest.raises(WorkflowValidationError, match="string literals"):
        validate_config_value(
            _expr('{1: "a"}'), allow_credentials=True, where="node 'x'"
        )


def test_literal_from_ast_round_trips() -> None:
    """``literal_from_ast`` returns the Python value of a literal node."""
    assert literal_from_ast(_expr('{"k": [1, 2]}')) == {"k": [1, 2]}
    assert literal_from_ast(_expr("-7")) == -7
