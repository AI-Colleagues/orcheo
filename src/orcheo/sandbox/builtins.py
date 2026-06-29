"""MicroPython builtin allowlist tied to the pinned WASM artifact.

The allowlist is the set of builtins that are both *supported by the pinned
artifact* and *safe to expose*. It was derived by probing the bundled
``micropython-wasi.wasm`` of ``micropython-wasm==0.1a2`` and then removing
dynamic/dangerous builtins (``eval``, ``exec``, ``compile``, ``open``,
``globals``, ``locals``, ``getattr``, ``setattr``, ``delattr``, ``dir``) and
``print``/``input`` (which would corrupt the stdout JSON protocol).

``CodeNode`` bodies are validated against this allowlist at ingestion so
unsupported usage (e.g. ``format``/``vars``, absent in the artifact) or
disallowed usage (e.g. ``eval``) fails before a run rather than inside the
sandbox. When the artifact version is bumped, re-probe and update both
``ARTIFACT_VERSION`` and these sets together.
"""

from __future__ import annotations
import ast
import builtins as _builtins
from collections.abc import Iterable
from orcheo.graph.ir.exceptions import WorkflowValidationError


# Distribution providing the bundled MicroPython-WASI artifact.
ARTIFACT_PACKAGE = "micropython-wasm"

# Pinned artifact version the allowlist is derived from.
ARTIFACT_VERSION = "0.1a2"

# Builtin callables present in the artifact and considered safe to expose.
_ALLOWED_FUNCTIONS = frozenset(
    {
        "abs",
        "all",
        "any",
        "bin",
        "bool",
        "bytearray",
        "bytes",
        "callable",
        "chr",
        "classmethod",
        "complex",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "frozenset",
        "hasattr",
        "hash",
        "hex",
        "id",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "oct",
        "ord",
        "pow",
        "property",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "staticmethod",
        "str",
        "sum",
        "super",
        "tuple",
        "type",
        "zip",
    }
)

# Exception/constant builtins present in the artifact.
_ALLOWED_EXCEPTIONS = frozenset(
    {
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "BaseException",
        "EOFError",
        "Exception",
        "GeneratorExit",
        "ImportError",
        "IndentationError",
        "IndexError",
        "KeyError",
        "KeyboardInterrupt",
        "LookupError",
        "MemoryError",
        "NameError",
        "NotImplementedError",
        "OSError",
        "OverflowError",
        "RuntimeError",
        "StopAsyncIteration",
        "StopIteration",
        "SyntaxError",
        "SystemExit",
        "TypeError",
        "UnicodeError",
        "ValueError",
        "ZeroDivisionError",
    }
)

# Names a CodeNode body may reference as builtins.
ALLOWED_BUILTINS = _ALLOWED_FUNCTIONS | _ALLOWED_EXCEPTIONS

# Names that are CPython builtins but are unsupported or unsafe in the sandbox.
# Referencing any of these in a body is a validation error.
_POLICED_NAMES = frozenset(dir(_builtins))


def validate_body_builtins(
    run_func: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    node_id: str,
) -> None:
    """Reject CodeNode bodies referencing unsupported/disallowed builtins.

    Any name that is a CPython builtin but not in :data:`ALLOWED_BUILTINS` is
    rejected: this covers both builtins absent from the artifact (``format``,
    ``vars``, …) and dangerous ones (``eval``, ``open``, ``getattr``, …). Local
    variables, parameters, and ``state``/``config``/``self`` are not builtin
    names and pass through untouched.

    Raises:
        WorkflowValidationError: On the first disallowed builtin reference.
    """
    for name, lineno in _referenced_names(run_func):
        if name in _POLICED_NAMES and name not in ALLOWED_BUILTINS:
            raise WorkflowValidationError(
                f"CodeNode '{node_id}' body uses builtin '{name}', which is not "
                f"supported by the sandbox (artifact {ARTIFACT_VERSION})",
                lineno=lineno,
            )


def _referenced_names(
    run_func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterable[tuple[str, int]]:
    """Yield ``(name, lineno)`` for every loaded name in the body."""
    for sub in ast.walk(run_func):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            yield sub.id, sub.lineno


__all__ = [
    "ALLOWED_BUILTINS",
    "ARTIFACT_PACKAGE",
    "ARTIFACT_VERSION",
    "validate_body_builtins",
]
