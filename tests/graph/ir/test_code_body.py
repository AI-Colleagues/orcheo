"""Tests for CodeNode body extraction and structural validation (Tasks 2.4, 2.6)."""

from __future__ import annotations
import ast
import pytest
from orcheo.graph.ir.code_body import extract_run_body, validate_code_body
from orcheo.graph.ir.exceptions import WorkflowValidationError


def _run_func(source: str) -> tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Parse a module and return its source plus the first ``run`` function."""
    module = ast.parse(source)
    for node in ast.walk(module):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "run"
        ):
            return source, node
    raise AssertionError("no run function found")


def test_extract_body_dedents_multiline() -> None:
    """A multi-line run body is extracted verbatim and dedented."""
    source, func = _run_func(
        "class X(CodeNode):\n"
        "    async def run(self, state, config):\n"
        "        score = state['x']\n"
        "        return {'results': {'y': score}}\n"
    )

    body = extract_run_body(source, func)

    assert body == "score = state['x']\nreturn {'results': {'y': score}}"


def test_extract_body_handles_single_line_def() -> None:
    """A one-line ``def run(...): return ...`` body is extracted correctly."""
    source, func = _run_func(
        "class X(CodeNode):\n    def run(self, state, config): return {'a': 1}\n"
    )

    body = extract_run_body(source, func)

    assert body == "return {'a': 1}"


def test_valid_body_passes() -> None:
    """A conforming body referencing only injected fields validates."""
    source, func = _run_func(
        "class X(CodeNode):\n"
        "    async def run(self, state, config):\n"
        "        return {'results': {'v': self.threshold}}\n"
    )

    validate_code_body(func, injected={"threshold"}, node_id="x")


def test_body_rejects_import() -> None:
    """A body containing an import is rejected."""
    source, func = _run_func(
        "class X(CodeNode):\n"
        "    async def run(self, state, config):\n"
        "        import os\n"
        "        return {'a': os.getcwd()}\n"
    )

    with pytest.raises(WorkflowValidationError, match="may not import"):
        validate_code_body(func, injected=set(), node_id="x")


def test_body_rejects_await() -> None:
    """A body using ``await`` is rejected."""
    source, func = _run_func(
        "class X(CodeNode):\n"
        "    async def run(self, state, config):\n"
        "        result = await something()\n"
        "        return {'a': result}\n"
    )

    with pytest.raises(WorkflowValidationError, match="await"):
        validate_code_body(func, injected=set(), node_id="x")


def test_body_rejects_undeclared_self_field() -> None:
    """Referencing a ``self`` field outside the injected set is rejected."""
    source, func = _run_func(
        "class X(CodeNode):\n"
        "    async def run(self, state, config):\n"
        "        return {'a': self.secret}\n"
    )

    with pytest.raises(WorkflowValidationError, match="undeclared field 'self.secret'"):
        validate_code_body(func, injected={"threshold"}, node_id="x")


def test_body_rejects_gadget_self_access() -> None:
    """A dunder gadget via ``self`` is rejected as an undeclared field."""
    source, func = _run_func(
        "class X(CodeNode):\n"
        "    async def run(self, state, config):\n"
        "        return {'a': self.__class__}\n"
    )

    with pytest.raises(WorkflowValidationError, match="undeclared field"):
        validate_code_body(func, injected=set(), node_id="x")


def test_body_requires_return_value() -> None:
    """A body that never returns a value is rejected."""
    source, func = _run_func(
        "class X(CodeNode):\n"
        "    async def run(self, state, config):\n"
        "        state['x'] = 1\n"
    )

    with pytest.raises(WorkflowValidationError, match="must return"):
        validate_code_body(func, injected=set(), node_id="x")
