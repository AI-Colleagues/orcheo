"""Validation of node config values against the config-value vocabulary.

Legal config values are:

* **JSON literals** — strings, numbers, booleans, ``None``, and (possibly
  nested) lists and dicts with string keys. Sets, bytes, complex numbers, and
  any expression that is not a literal are rejected.
* **``{{state.path}}`` templates** — accepted anywhere (inside string literals);
  they stay inert in the IR and are resolved by the trusted decoder at run time.
* **``[[credential]]`` placeholders** — accepted only in built-in node config and
  rejected in ``CodeNode`` injected config, so the sandbox never receives
  resolved secrets.

The validator walks the value's AST so it can run during interpretation without
executing any author code. ``literal_from_ast`` converts a validated value node
to its Python value via :func:`ast.literal_eval` (still no execution).
"""

from __future__ import annotations
import ast
import re
from typing import Any
from orcheo.graph.ir.exceptions import WorkflowValidationError


# Matches a ``[[credential]]`` placeholder anywhere within a string.
_CREDENTIAL_PLACEHOLDER = re.compile(r"\[\[.+?\]\]")


def contains_credential_placeholder(value: str) -> bool:
    """Return ``True`` when ``value`` embeds a ``[[credential]]`` placeholder."""
    return _CREDENTIAL_PLACEHOLDER.search(value) is not None


def validate_config_value(
    node: ast.expr,
    *,
    allow_credentials: bool,
    where: str,
) -> None:
    """Validate a config value expression node against the vocabulary.

    Args:
        node: The AST expression supplied as a config value.
        allow_credentials: Whether ``[[credential]]`` placeholders are permitted
            (true for built-in node config, false for ``CodeNode`` config).
        where: Human-readable context for error messages, e.g. ``"node 'x'"``.

    Raises:
        WorkflowValidationError: When the value is not a JSON literal or embeds a
            disallowed ``[[credential]]`` placeholder.
    """
    _check_value(node, allow_credentials=allow_credentials, where=where)


def literal_from_ast(node: ast.expr) -> Any:
    """Return the Python value of a validated literal value node.

    Raises:
        WorkflowValidationError: When the node is unexpectedly not a literal
            (should not happen after :func:`validate_config_value`).
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError) as exc:  # pragma: no cover - guarded
        msg = f"config value is not a JSON literal: {exc}"
        raise WorkflowValidationError(
            msg, lineno=getattr(node, "lineno", None)
        ) from exc


def _check_value(node: ast.expr, *, allow_credentials: bool, where: str) -> None:
    """Recursively validate a literal value node."""
    if isinstance(node, ast.Constant):
        _check_constant(node, allow_credentials=allow_credentials, where=where)
        return
    if isinstance(node, ast.List | ast.Tuple):
        for element in node.elts:
            _check_value(element, allow_credentials=allow_credentials, where=where)
        return
    if isinstance(node, ast.Dict):
        _check_dict(node, allow_credentials=allow_credentials, where=where)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        if isinstance(node.operand, ast.Constant) and isinstance(
            node.operand.value, int | float
        ):
            return
    raise WorkflowValidationError(
        f"{where}: config values must be JSON literals, '{{{{state.path}}}}' "
        "templates, or '[[credential]]' placeholders",
        lineno=getattr(node, "lineno", None),
    )


def _check_constant(node: ast.Constant, *, allow_credentials: bool, where: str) -> None:
    """Validate a constant: JSON scalar, with the per-layer credential rule."""
    value = node.value
    if isinstance(value, bool) or value is None or isinstance(value, int | float):
        return
    if isinstance(value, str):
        if not allow_credentials and contains_credential_placeholder(value):
            msg = (
                f"{where}: '[[credential]]' placeholders are not allowed in "
                "CodeNode config"
            )
            raise WorkflowValidationError(msg, lineno=node.lineno)
        return
    raise WorkflowValidationError(
        f"{where}: unsupported config literal of type {type(value).__name__}",
        lineno=node.lineno,
    )


def _check_dict(node: ast.Dict, *, allow_credentials: bool, where: str) -> None:
    """Validate a dict literal: string keys and literal values."""
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            raise WorkflowValidationError(
                f"{where}: dict unpacking is not allowed in config",
                lineno=getattr(node, "lineno", None),
            )
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            raise WorkflowValidationError(
                f"{where}: config dict keys must be string literals",
                lineno=getattr(key, "lineno", None),
            )
        _check_value(value, allow_credentials=allow_credentials, where=where)


__all__ = [
    "contains_credential_placeholder",
    "literal_from_ast",
    "validate_config_value",
]
