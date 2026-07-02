"""Restricted-mode schema-class validation and lowering."""

from __future__ import annotations
import ast
from dataclasses import dataclass
from typing import Any, Literal
from pydantic import BaseModel, Field, create_model
from orcheo.graph.ir.config_values import literal_from_ast, validate_config_value
from orcheo.graph.ir.exceptions import WorkflowValidationError


SCHEMA_BASE = "BaseModel"
FIELD_HELPER = "Field"
_REQUIRED = Ellipsis


def is_schema_class(stmt: ast.ClassDef) -> bool:
    """Return ``True`` when ``stmt`` is a ``BaseModel`` schema declaration."""
    return (
        len(stmt.bases) == 1
        and isinstance(stmt.bases[0], ast.Name)
        and (stmt.bases[0].id == SCHEMA_BASE)
    )


def validate_schema_class(stmt: ast.ClassDef) -> None:
    """Validate a restricted-mode ``BaseModel`` schema declaration."""
    if stmt.decorator_list:
        raise WorkflowValidationError(
            f"decorators are not allowed on schema '{stmt.name}'", lineno=stmt.lineno
        )
    if stmt.keywords:
        raise WorkflowValidationError(
            f"metaclass / class keywords are not allowed on schema '{stmt.name}'",
            lineno=stmt.lineno,
        )
    if not is_schema_class(stmt):
        raise WorkflowValidationError(
            f"class '{stmt.name}' must inherit only from {SCHEMA_BASE}",
            lineno=stmt.lineno,
        )

    for member in stmt.body:
        if _is_docstring(member):
            continue
        if not isinstance(member, ast.AnnAssign):
            raise WorkflowValidationError(
                f"schema '{stmt.name}' may only declare annotated fields",
                lineno=getattr(member, "lineno", None),
            )
        if not isinstance(member.target, ast.Name):
            raise WorkflowValidationError(
                f"schema '{stmt.name}' fields must be simple annotated assignments",
                lineno=member.lineno,
            )
        if member.target.id.startswith("_"):
            raise WorkflowValidationError(
                f"schema '{stmt.name}' field '{member.target.id}' may not start "
                "with '_'",
                lineno=member.lineno,
            )
        if member.value is not None:
            _validate_schema_field_value(member.value, field_name=member.target.id)


def schema_json_schema(
    name: str,
    classes: dict[str, ast.ClassDef],
) -> dict[str, Any]:
    """Lower one validated schema class to a JSON Schema mapping."""
    compiler = _SchemaCompiler(classes)
    return compiler.json_schema(name)


@dataclass
class _SchemaCompiler:
    """Lower schema classes to dynamic Pydantic models without author execution."""

    classes: dict[str, ast.ClassDef]

    def __post_init__(self) -> None:
        self._models: dict[str, type[BaseModel]] = {}
        self._stack: list[str] = []

    def json_schema(self, name: str) -> dict[str, Any]:
        """Return ``name`` as a JSON Schema mapping."""
        return self._build_model(name).model_json_schema()

    def _build_model(self, name: str) -> type[BaseModel]:
        if name in self._models:
            return self._models[name]
        schema = self.classes.get(name)
        if schema is None:
            raise WorkflowValidationError(f"unknown schema '{name}'")
        if name in self._stack:
            cycle = " -> ".join([*self._stack, name])
            raise WorkflowValidationError(
                f"recursive schema references are not supported: {cycle}",
                lineno=schema.lineno,
            )

        self._stack.append(name)
        try:
            fields: dict[str, tuple[Any, Any]] = {}
            for member in schema.body:
                if _is_docstring(member):
                    continue
                if not isinstance(member, ast.AnnAssign) or not isinstance(
                    member.target, ast.Name
                ):
                    continue  # pragma: no cover - guarded by validate_schema_class
                field_name = member.target.id
                annotation = self._annotation_from_ast(member.annotation)
                default = _field_default_from_ast(member.value, field_name=field_name)
                fields[field_name] = (annotation, default)
            model = create_model(name, __base__=BaseModel, **fields)
            self._models[name] = model
            return model
        finally:
            self._stack.pop()

    def _annotation_from_ast(self, node: ast.expr) -> Any:  # noqa: C901, PLR0911
        if isinstance(node, ast.Name):
            if node.id == "str":
                return str
            if node.id == "int":
                return int
            if node.id == "float":
                return float
            if node.id == "bool":
                return bool
            if node.id == "Any":
                return Any
            if node.id in self.classes:
                return self._build_model(node.id)
            raise WorkflowValidationError(
                f"unsupported schema annotation '{node.id}'",
                lineno=getattr(node, "lineno", None),
            )
        if isinstance(node, ast.Constant) and node.value is None:
            return type(None)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._annotation_from_ast(node.left) | self._annotation_from_ast(
                node.right
            )
        if isinstance(node, ast.Subscript):
            return self._subscript_annotation(node)
        raise WorkflowValidationError(
            "unsupported schema annotation",
            lineno=getattr(node, "lineno", None),
        )

    def _subscript_annotation(self, node: ast.Subscript) -> Any:
        if not isinstance(node.value, ast.Name):
            raise WorkflowValidationError(
                "unsupported schema annotation",
                lineno=getattr(node, "lineno", None),
            )
        base = node.value.id
        if base == "list":
            return list[self._annotation_from_ast(node.slice)]
        if base == "dict":
            key_node, value_node = _tuple_slice(node.slice, expected=2)
            return dict[
                self._annotation_from_ast(key_node),
                self._annotation_from_ast(value_node),
            ]
        if base == "Literal":
            literal_nodes = (
                list(node.slice.elts)
                if isinstance(node.slice, ast.Tuple)
                else [node.slice]
            )
            literal_values = tuple(_literal_value(item) for item in literal_nodes)
            return Literal.__getitem__(literal_values)
        raise WorkflowValidationError(
            f"unsupported schema annotation '{base}[...]'",
            lineno=getattr(node, "lineno", None),
        )


def _field_default_from_ast(node: ast.expr | None, *, field_name: str) -> Any:
    if node is None:
        return _REQUIRED
    if isinstance(node, ast.Call):
        return _field_call_from_ast(node, field_name=field_name)
    validate_config_value(node, allow_credentials=False, where=f"field '{field_name}'")
    return literal_from_ast(node)


def _field_call_from_ast(node: ast.Call, *, field_name: str) -> Any:
    if not isinstance(node.func, ast.Name) or node.func.id != FIELD_HELPER:
        raise WorkflowValidationError(
            f"field '{field_name}': only {FIELD_HELPER}(...) defaults are supported",
            lineno=getattr(node, "lineno", None),
        )
    if len(node.args) > 1:
        raise WorkflowValidationError(
            f"field '{field_name}': Field(...) accepts at most one positional default",
            lineno=getattr(node, "lineno", None),
        )
    args: list[Any] = []
    if node.args:
        validate_config_value(
            node.args[0], allow_credentials=False, where=f"field '{field_name}'"
        )
        args.append(literal_from_ast(node.args[0]))
    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            raise WorkflowValidationError(
                f"field '{field_name}': Field(...) may not use ** unpacking",
                lineno=getattr(node, "lineno", None),
            )
        validate_config_value(
            kw.value,
            allow_credentials=False,
            where=f"field '{field_name}'",
        )
        kwargs[kw.arg] = literal_from_ast(kw.value)
    return Field(*args, **kwargs)


def _validate_schema_field_value(node: ast.expr, *, field_name: str) -> None:
    if isinstance(node, ast.Call):
        _field_call_from_ast(node, field_name=field_name)
        return
    validate_config_value(node, allow_credentials=False, where=f"field '{field_name}'")


def _literal_value(node: ast.expr) -> Any:
    validate_config_value(node, allow_credentials=False, where="schema literal")
    return literal_from_ast(node)


def _tuple_slice(node: ast.expr, *, expected: int) -> tuple[ast.expr, ...]:
    if not isinstance(node, ast.Tuple) or len(node.elts) != expected:
        raise WorkflowValidationError(
            "unsupported schema annotation",
            lineno=getattr(node, "lineno", None),
        )
    return tuple(node.elts)


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


__all__ = [
    "FIELD_HELPER",
    "SCHEMA_BASE",
    "is_schema_class",
    "schema_json_schema",
    "validate_schema_class",
]
