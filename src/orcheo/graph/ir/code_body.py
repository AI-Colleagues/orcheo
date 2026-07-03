"""Extraction and structural validation of ``CodeNode.run`` bodies.

The body of each ``CodeNode.run`` is the only author code carried in the IR. It
is extracted as a dedented string via AST source slicing (never
``inspect.getsource``, which would require importing the module) and validated
structurally so unsupported usage fails at ingestion rather than inside the
sandbox:

* no ``import`` statements,
* no ``await`` / async constructs (the body is a pure synchronous transform),
* no generator ``yield``,
* at least one ``return`` of a value (the node result payload), and
* ``self.<field>`` access restricted to the node's injected fields.

The MicroPython builtin allowlist check is layered on top in Milestone 3.
"""

from __future__ import annotations
import ast
import textwrap
from collections.abc import Iterable, Iterator
from orcheo.graph.ir.exceptions import WorkflowValidationError


RunFunction = ast.FunctionDef | ast.AsyncFunctionDef


def extract_run_body(source: str, run_func: RunFunction) -> str:
    """Return the dedented source of a ``run`` method body.

    Uses line/AST spans from ``source`` rather than importing the module, so no
    author code executes during extraction.
    """
    body = run_func.body
    first, last = body[0], body[-1]

    if first.lineno == run_func.lineno:
        # Body begins on the ``def`` line (e.g. a one-liner). Rebuild from each
        # statement's own source segment to avoid capturing the signature.
        segments = [
            segment
            for stmt in body
            if (segment := ast.get_source_segment(source, stmt)) is not None
        ]
        return textwrap.dedent("\n".join(segments)).strip("\n")

    lines = source.splitlines()
    snippet = "\n".join(lines[first.lineno - 1 : last.end_lineno])
    return textwrap.dedent(snippet).strip("\n")


def validate_code_body(
    run_func: RunFunction,
    *,
    injected: Iterable[str],
    node_id: str,
) -> None:
    """Structurally validate a ``CodeNode.run`` body.

    Args:
        run_func: The ``run`` method AST node.
        injected: Field names the body may reference as ``self.<field>``.
        node_id: Node id used in error messages.

    Raises:
        WorkflowValidationError: On any disallowed construct or undeclared field.
    """
    injected_set = set(injected)
    _reject_disallowed_constructs(run_func, node_id=node_id)
    _check_self_references(run_func, injected=injected_set, node_id=node_id)
    _require_return_value(run_func, node_id=node_id)


def _reject_disallowed_constructs(run_func: RunFunction, *, node_id: str) -> None:
    """Reject imports, await/async, yield, and dunder access anywhere in the body."""
    for sub in ast.walk(run_func):
        if isinstance(sub, ast.Import | ast.ImportFrom):
            raise WorkflowValidationError(
                f"CodeNode '{node_id}' body may not import modules", lineno=sub.lineno
            )
        if isinstance(sub, ast.Name) and sub.id.startswith("__"):
            raise WorkflowValidationError(
                f"CodeNode '{node_id}' body may not reference dunder name "
                f"'{sub.id}'; this closes builtin-allowlist escapes such as "
                "__builtins__ and __import__",
                lineno=sub.lineno,
            )
        if isinstance(sub, ast.Attribute) and sub.attr.startswith("__"):
            raise WorkflowValidationError(
                f"CodeNode '{node_id}' body may not access dunder attribute "
                f"'{sub.attr}'; this closes introspection gadget chains "
                "(e.g. __class__/__subclasses__/__globals__)",
                lineno=sub.lineno,
            )
        if isinstance(sub, ast.Await):
            raise WorkflowValidationError(
                f"CodeNode '{node_id}' body may not use 'await'; it must be a pure "
                "synchronous transform",
                lineno=sub.lineno,
            )
        if isinstance(sub, ast.AsyncFor | ast.AsyncWith):
            raise WorkflowValidationError(
                f"CodeNode '{node_id}' body may not use async constructs",
                lineno=sub.lineno,
            )
        if isinstance(sub, ast.AsyncFunctionDef) and sub is not run_func:
            raise WorkflowValidationError(
                f"CodeNode '{node_id}' body may not define async functions",
                lineno=sub.lineno,
            )
        if isinstance(sub, ast.Yield | ast.YieldFrom):
            raise WorkflowValidationError(
                f"CodeNode '{node_id}' body may not be a generator", lineno=sub.lineno
            )


def _check_self_references(
    run_func: RunFunction, *, injected: set[str], node_id: str
) -> None:
    """Ensure every ``self.<attr>`` access names an injected field."""
    for sub in ast.walk(run_func):
        if (
            isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "self"
            and sub.attr not in injected
        ):
            allowed = ", ".join(sorted(injected)) or "(none)"
            raise WorkflowValidationError(
                f"CodeNode '{node_id}' body references undeclared field "
                f"'self.{sub.attr}'; injected fields: {allowed}",
                lineno=sub.lineno,
            )


def _require_return_value(run_func: RunFunction, *, node_id: str) -> None:
    """Require at least one ``return <value>`` in ``run``'s own scope.

    The walk deliberately stops at nested function boundaries: a ``return``
    inside a helper defined within ``run`` would leave ``run`` itself returning
    ``None`` (a runtime ``SandboxOutputError``), so it must not satisfy the
    ingestion-time check.
    """
    has_return_value = any(
        isinstance(sub, ast.Return) and sub.value is not None
        for sub in _walk_own_scope(run_func)
    )
    if not has_return_value:
        raise WorkflowValidationError(
            f"CodeNode '{node_id}' body must return a node result payload",
            lineno=run_func.lineno,
        )


def _walk_own_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Yield descendants of ``node`` without entering nested function scopes.

    Unlike :func:`ast.walk`, descent stops at nested ``def`` / ``async def`` /
    ``lambda`` boundaries, so statements belonging to a helper function are not
    attributed to the enclosing scope.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        yield child
        yield from _walk_own_scope(child)


__all__ = ["RunFunction", "extract_run_body", "validate_code_body"]
