"""Restricted grammar validator for ``workflow.py`` (definition layer).

The validator parses a ``workflow.py`` to an AST and rejects anything outside a
small declarative grammar, *executing no author code*. The boundary is the
allowlist, not a denylist: only Orcheo imports, ``CodeNode`` subclasses, node /
edge instantiation, ``add_node`` / ``add_edge`` / ``add_conditional_edges`` /
``set_entry_point`` / ``set_finish_point`` / ``compile``, and a single
zero-argument ``orcheo_workflow`` entrypoint (``def`` or ``async def``) are
permitted.

``CodeNode.run`` method *bodies* are intentionally treated as opaque here — they
become author code that runs only inside the sandbox, and are validated by
:mod:`orcheo.graph.ir.code_body` and the builtin allowlist instead. Everything
else (the entrypoint and class-level config) is *construction-time* code and is
swept for gadget chains: dunder/underscore access, dynamic subscripts, lambdas,
comprehensions, starred args, and await/yield are all rejected.
"""

from __future__ import annotations
import ast
from orcheo.graph.ir.config_values import validate_config_value
from orcheo.graph.ir.exceptions import WorkflowValidationError
from orcheo.graph.ir.schemas import is_schema_class, validate_schema_class


# The single required workflow entrypoint function name.
ENTRYPOINT_NAME = "orcheo_workflow"

# The only permitted base class for user-defined node logic.
CODENODE_BASE = "CodeNode"

# Graph-assembly methods permitted as statements in graph-builder functions.
GRAPH_METHODS = frozenset(
    {
        "add_node",
        "add_edge",
        "add_conditional_edges",
        "set_entry_point",
        "set_finish_point",
    }
)

_COMPILE_METHOD = "compile"


def validate_grammar(module: ast.Module) -> None:
    """Validate a parsed ``workflow.py`` against the restricted grammar.

    Raises:
        WorkflowValidationError: On the first disallowed construct, with a line
            reference where available.
    """
    entrypoints: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    for stmt in module.body:
        if _is_docstring(stmt):
            continue
        if isinstance(stmt, ast.Import | ast.ImportFrom):
            _validate_import(stmt)
        elif isinstance(stmt, ast.ClassDef):
            _validate_class(stmt)
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            _validate_graph_builder(stmt)
            if stmt.name == ENTRYPOINT_NAME:
                entrypoints.append(stmt)
        else:
            raise WorkflowValidationError(
                "only Orcheo imports, restricted schema / CodeNode classes, and "
                "graph-builder functions are allowed at module level",
                lineno=getattr(stmt, "lineno", None),
            )

    _require_single_entrypoint(entrypoints)


def _require_single_entrypoint(
    entrypoints: list[ast.FunctionDef | ast.AsyncFunctionDef],
) -> None:
    """Ensure exactly one ``orcheo_workflow`` entrypoint was defined."""
    if not entrypoints:
        raise WorkflowValidationError(
            f"script must define exactly one zero-argument '{ENTRYPOINT_NAME}' "
            "entrypoint"
        )
    if len(entrypoints) > 1:
        raise WorkflowValidationError(
            f"script must define exactly one '{ENTRYPOINT_NAME}' entrypoint",
            lineno=entrypoints[1].lineno,
        )


def _is_docstring(stmt: ast.stmt) -> bool:
    """Return ``True`` for a bare string-constant expression (docstring)."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _validate_import(stmt: ast.Import | ast.ImportFrom) -> None:
    """Allow only absolute Orcheo imports; reject star and non-Orcheo imports."""
    if isinstance(stmt, ast.ImportFrom):
        if stmt.level and stmt.level > 0:
            raise WorkflowValidationError(
                "relative imports are not allowed", lineno=stmt.lineno
            )
        if not _is_orcheo_module(stmt.module):
            raise WorkflowValidationError(
                f"imports must come from Orcheo; '{stmt.module}' is not allowed",
                lineno=stmt.lineno,
            )
        for alias in stmt.names:
            if alias.name == "*":
                raise WorkflowValidationError(
                    "star imports are not allowed", lineno=stmt.lineno
                )
        return
    for alias in stmt.names:
        if not _is_orcheo_module(alias.name):
            raise WorkflowValidationError(
                f"imports must come from Orcheo; '{alias.name}' is not allowed",
                lineno=stmt.lineno,
            )


def _is_orcheo_module(module: str | None) -> bool:
    """Return ``True`` when ``module`` is the ``orcheo`` package or a submodule."""
    return module is not None and (module == "orcheo" or module.startswith("orcheo."))


def _validate_class(stmt: ast.ClassDef) -> None:
    """Validate a restricted-mode class declaration."""
    if is_schema_class(stmt):
        validate_schema_class(stmt)
        return
    _validate_codenode_class(stmt)


def _validate_codenode_class(stmt: ast.ClassDef) -> None:
    """Validate a ``class X(CodeNode)`` declaration and its body."""
    if stmt.decorator_list:
        raise WorkflowValidationError(
            f"decorators are not allowed on class '{stmt.name}'", lineno=stmt.lineno
        )
    if stmt.keywords:
        raise WorkflowValidationError(
            f"metaclass / class keywords are not allowed on '{stmt.name}'",
            lineno=stmt.lineno,
        )
    if len(stmt.bases) != 1 or not (
        isinstance(stmt.bases[0], ast.Name) and stmt.bases[0].id == CODENODE_BASE
    ):
        raise WorkflowValidationError(
            f"class '{stmt.name}' must inherit only from {CODENODE_BASE}",
            lineno=stmt.lineno,
        )

    has_run = False
    for member in stmt.body:
        if _is_docstring(member):
            continue
        if isinstance(member, ast.AnnAssign | ast.Assign):
            _validate_class_field(member, class_name=stmt.name)
        elif isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
            has_run = _validate_class_method(member, class_name=stmt.name) or has_run
        else:
            raise WorkflowValidationError(
                f"class '{stmt.name}' may only declare config fields and a 'run' "
                "method",
                lineno=getattr(member, "lineno", None),
            )

    if not has_run:
        raise WorkflowValidationError(
            f"class '{stmt.name}' must define a 'run' method", lineno=stmt.lineno
        )


def _validate_class_method(
    member: ast.FunctionDef | ast.AsyncFunctionDef, *, class_name: str
) -> bool:
    """Validate a class method; return ``True`` if it is the ``run`` method."""
    if member.name != "run":
        raise WorkflowValidationError(
            f"class '{class_name}' may only define a 'run' method, not "
            f"'{member.name}'; define '{member.name}' as a nested function "
            "inside 'run' instead",
            lineno=member.lineno,
        )
    if member.decorator_list:
        raise WorkflowValidationError(
            f"decorators are not allowed on '{class_name}.run'", lineno=member.lineno
        )
    return True


def _validate_class_field(
    member: ast.AnnAssign | ast.Assign, *, class_name: str
) -> None:
    """Validate a class-level config field default value (literal, no creds)."""
    if isinstance(member, ast.Assign):
        if len(member.targets) != 1 or not isinstance(member.targets[0], ast.Name):
            raise WorkflowValidationError(
                f"class '{class_name}' fields must be simple assignments",
                lineno=member.lineno,
            )
        target = member.targets[0]
        value: ast.expr | None = member.value
    else:
        if not isinstance(member.target, ast.Name):
            raise WorkflowValidationError(
                f"class '{class_name}' fields must be simple assignments",
                lineno=member.lineno,
            )
        target = member.target
        value = member.value

    if target.id.startswith("_"):
        raise WorkflowValidationError(
            f"class '{class_name}' field '{target.id}' may not start with '_'",
            lineno=member.lineno,
        )
    if value is not None:
        validate_config_value(
            value, allow_credentials=False, where=f"field '{target.id}'"
        )


def _validate_graph_builder(func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
    """Validate a zero-argument graph-builder function body."""
    if func.decorator_list:
        raise WorkflowValidationError(
            f"decorators are not allowed on graph builder '{func.name}'",
            lineno=func.lineno,
        )
    args = func.args
    if args.args or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg:
        raise WorkflowValidationError(
            f"graph builder '{func.name}' must take zero arguments",
            lineno=func.lineno,
        )

    for stmt in func.body:
        _validate_graph_builder_statement(stmt, func_name=func.name)
    _gadget_sweep(func.body)


def _validate_graph_builder_statement(stmt: ast.stmt, *, func_name: str) -> None:
    """Allow only graph-assembly statements inside a graph builder."""
    if _is_docstring(stmt):
        return
    if isinstance(stmt, ast.Assign):
        _validate_entrypoint_assign(stmt)
        return
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        _validate_graph_method_call(stmt.value)
        return
    if isinstance(stmt, ast.Return):
        _validate_graph_builder_return(stmt, func_name=func_name)
        return
    raise WorkflowValidationError(
        f"graph builder '{func_name}' may only build and wire the graph; this "
        "construct is not allowed",
        lineno=getattr(stmt, "lineno", None),
    )


def _validate_entrypoint_assign(stmt: ast.Assign) -> None:
    """Allow ``var = StateGraph(...)`` or ``var = SomeNode(...)`` assignments."""
    if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
        raise WorkflowValidationError(
            "only single-name assignments are allowed in the entrypoint",
            lineno=stmt.lineno,
        )
    if not isinstance(stmt.value, ast.Call):
        raise WorkflowValidationError(
            "entrypoint assignments must construct a graph or node",
            lineno=stmt.lineno,
        )
    if not isinstance(stmt.value.func, ast.Name):
        raise WorkflowValidationError(
            "entrypoint assignments must call a graph or node class directly",
            lineno=stmt.lineno,
        )


def _validate_graph_method_call(call: ast.Call) -> None:
    """Validate a ``graph.<method>(...)`` expression statement."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
        raise WorkflowValidationError(
            "only graph-assembly method calls are allowed as statements",
            lineno=getattr(call, "lineno", None),
        )
    if func.attr not in GRAPH_METHODS:
        raise WorkflowValidationError(
            f"method '{func.attr}' is not an allowed graph-assembly method",
            lineno=getattr(call, "lineno", None),
        )


def _validate_graph_builder_return(stmt: ast.Return, *, func_name: str) -> None:
    """Allow ``return <name>`` or ``return <name>.compile()``."""
    value = stmt.value
    if value is None:
        raise WorkflowValidationError(
            f"graph builder '{func_name}' must return the assembled graph",
            lineno=stmt.lineno,
        )
    if isinstance(value, ast.Name):
        return
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == _COMPILE_METHOD
        and isinstance(value.func.value, ast.Name)
    ):
        return
    raise WorkflowValidationError(
        f"graph builder '{func_name}' must return the graph or 'graph.compile()'",
        lineno=stmt.lineno,
    )


def _gadget_sweep(body: list[ast.stmt]) -> None:
    """Reject gadget-chain constructs anywhere in construction-time code."""
    for stmt in body:
        for node in ast.walk(stmt):
            _reject_gadget_node(node)


def _reject_gadget_node(node: ast.AST) -> None:
    """Raise if ``node`` is a known gadget/dynamic-access construct."""
    if isinstance(node, ast.Lambda):
        raise WorkflowValidationError(
            "lambdas are not allowed", lineno=getattr(node, "lineno", None)
        )
    if isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
        raise WorkflowValidationError(
            "comprehensions are not allowed in construction code",
            lineno=getattr(node, "lineno", None),
        )
    if isinstance(node, ast.Subscript):
        raise WorkflowValidationError(
            "subscript access is not allowed in construction code",
            lineno=getattr(node, "lineno", None),
        )
    if isinstance(node, ast.Starred):
        raise WorkflowValidationError(
            "starred / unpacking arguments are not allowed",
            lineno=getattr(node, "lineno", None),
        )
    if isinstance(node, ast.Await | ast.Yield | ast.YieldFrom):
        raise WorkflowValidationError(
            "await / yield are not allowed in construction code",
            lineno=getattr(node, "lineno", None),
        )
    if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
        raise WorkflowValidationError(
            f"access to private/dunder attribute '{node.attr}' is not allowed",
            lineno=getattr(node, "lineno", None),
        )
    if isinstance(node, ast.Name) and node.id.startswith("_"):
        raise WorkflowValidationError(
            f"private/underscore name '{node.id}' is not allowed",
            lineno=getattr(node, "lineno", None),
        )


__all__ = [
    "CODENODE_BASE",
    "ENTRYPOINT_NAME",
    "GRAPH_METHODS",
    "validate_grammar",
]
