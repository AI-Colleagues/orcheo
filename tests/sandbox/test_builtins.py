"""Tests for the MicroPython builtin allowlist (Task 3.4)."""

from __future__ import annotations
import ast
import pytest
from orcheo.graph.ir.exceptions import WorkflowValidationError
from orcheo.sandbox.builtins import ALLOWED_BUILTINS, validate_body_builtins


def _run_func(body: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Wrap ``body`` in a run method and return its AST node."""
    source = "class X(CodeNode):\n    async def run(self, state, config):\n"
    source += "\n".join(f"        {line}" for line in body.splitlines())
    module = ast.parse(source)
    node = module.body[0].body[0]  # type: ignore[attr-defined]
    assert isinstance(node, ast.AsyncFunctionDef)
    return node


def test_allows_supported_builtins() -> None:
    """A body using only allowlisted builtins validates."""
    func = _run_func("return {'n': len(sorted(state['items']))}")
    validate_body_builtins(func, node_id="x")


def test_allows_exception_names() -> None:
    """A body raising a supported exception validates."""
    func = _run_func(
        "if not state:\n    raise ValueError('empty')\nreturn {'ok': True}"
    )
    validate_body_builtins(func, node_id="x")


@pytest.mark.parametrize("name", ["eval", "exec", "open", "getattr", "globals"])
def test_rejects_dangerous_builtins(name: str) -> None:
    """Dangerous dynamic builtins are rejected even though MP provides them."""
    func = _run_func(f"return {{'r': {name}}}")
    with pytest.raises(WorkflowValidationError, match=f"builtin '{name}'"):
        validate_body_builtins(func, node_id="x")


@pytest.mark.parametrize("name", ["format", "vars"])
def test_rejects_unsupported_builtins(name: str) -> None:
    """Builtins absent from the artifact are rejected."""
    func = _run_func(f"return {{'r': {name}(state)}}")
    with pytest.raises(WorkflowValidationError, match="not supported by the sandbox"):
        validate_body_builtins(func, node_id="x")


def test_print_is_not_allowed() -> None:
    """``print`` is disallowed so it cannot corrupt the stdout protocol."""
    assert "print" not in ALLOWED_BUILTINS
    func = _run_func("print('hi')\nreturn {}")
    with pytest.raises(WorkflowValidationError, match="builtin 'print'"):
        validate_body_builtins(func, node_id="x")


def test_local_names_are_not_policed() -> None:
    """Local variables sharing no builtin name pass through untouched."""
    func = _run_func("total = state['a'] + state['b']\nreturn {'total': total}")
    validate_body_builtins(func, node_id="x")
