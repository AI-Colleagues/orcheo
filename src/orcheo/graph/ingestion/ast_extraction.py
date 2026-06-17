"""AST-based metadata extraction for LangGraph workflow scripts."""

from __future__ import annotations
import ast
import importlib
import re
from functools import lru_cache
from typing import Any
from pydantic import BaseModel
from pydantic_core import PydanticUndefined


_CRON_NODE_TYPE = "CronTriggerNode"
_CRON_FIELDS: frozenset[str] = frozenset(
    {"expression", "timezone", "allow_overlapping", "start_at", "end_at"}
)

_LISTENER_NODE_PLATFORMS: dict[str, str] = {
    "TelegramBotListenerNode": "telegram",
    "DiscordBotListenerNode": "discord",
    "QQBotListenerNode": "qq",
}

_CREDENTIAL_PLACEHOLDER_PATTERN = re.compile(r"^\[\[(?P<body>.+)\]\]$")
_ORCHEO_NODE_MODULE_PREFIX = "orcheo.nodes"


def _literal_value(node: ast.expr) -> Any:
    """Return the Python literal value of a constant AST node, or None."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        val = _literal_value(node.operand)
        if isinstance(val, (int, float)):
            return -val
    # Handle literal collections (lists, dicts, tuples)
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _extract_kwargs(call_node: ast.Call) -> dict[str, Any]:
    """Extract literal keyword arguments from a Call node."""
    result: dict[str, Any] = {}
    for kw in call_node.keywords:
        if kw.arg is None:
            continue
        val = _literal_value(kw.value)
        if val is not None:
            result[kw.arg] = val
    return result


def _get_call_name(node: ast.expr) -> str | None:
    """Return the function/class name from a Call's func field."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class _AddNodeVisitor(ast.NodeVisitor):
    """Walk an AST to collect graph.add_node() call metadata."""

    def __init__(self) -> None:
        self.import_aliases: dict[str, str] = {}
        self.import_modules: dict[str, str] = {}
        self.cron_entries: list[dict[str, Any]] = []
        self.listener_entries: list[dict[str, Any]] = []
        self.credential_entries: list[dict[str, str]] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name == "*":
                continue
            self.import_aliases[alias.asname or alias.name] = alias.name
            if node.module is not None:
                self.import_modules[alias.asname or alias.name] = node.module
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self._handle_constructor_call(node)
        self._handle_add_node_call(node)
        self.generic_visit(node)

    def _resolve_class_name(self, node: ast.expr) -> str | None:
        class_name = _get_call_name(node)
        if class_name is None:
            return None
        return self.import_aliases.get(class_name, class_name)

    def _handle_constructor_call(self, call: ast.Call) -> None:
        class_name = self._resolve_class_name(call.func)
        if class_name is None:
            return

        provided_fields = {kw.arg for kw in call.keywords if kw.arg is not None}
        module_name = _get_call_module(call.func)
        if module_name is None:
            raw_name = _get_call_name(call.func)
            if raw_name is not None:
                module_name = self.import_modules.get(raw_name)
        self.credential_entries.extend(
            _extract_default_credentials(
                class_name,
                provided_fields,
                module_name=module_name,
            )
        )

    def _handle_add_node_call(self, call: ast.Call) -> None:
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_node"):
            return
        if len(call.args) < 2:  # noqa: PLR2004
            return

        name_node = call.args[0]
        ctor_node = call.args[1]

        if not isinstance(name_node, ast.Constant) or not isinstance(
            name_node.value, str
        ):
            return
        node_name = name_node.value

        if not isinstance(ctor_node, ast.Call):
            return
        class_name = self._resolve_class_name(ctor_node.func)
        if class_name is None:
            return

        if class_name == _CRON_NODE_TYPE:
            kwargs = _extract_kwargs(ctor_node)
            entry = {k: kwargs[k] for k in _CRON_FIELDS if k in kwargs}
            if entry:
                self.cron_entries.append(entry)

        elif class_name in _LISTENER_NODE_PLATFORMS or class_name.endswith(
            "ListenerNode"
        ):
            kwargs = _extract_kwargs(ctor_node)
            platform = kwargs.get("platform") or _LISTENER_NODE_PLATFORMS.get(
                class_name, ""
            )
            entry = {
                "node_name": node_name,
                "type": class_name,
                "platform": platform,
            }
            entry.update({k: v for k, v in kwargs.items() if k != "platform"})
            self.listener_entries.append(entry)


def _extract_default_credentials(
    class_name: str,
    provided_fields: set[str],
    *,
    module_name: str | None = None,
) -> list[dict[str, str]]:
    """Return credential placeholders from node defaults not overridden in source."""
    node_cls = _resolve_orcheo_node_class(class_name, module_name)
    if node_cls is None:
        return []
    return _collect_model_default_credentials(class_name, node_cls, provided_fields)


def _collect_model_default_credentials(
    class_name: str,
    node_cls: type[BaseModel],
    provided_fields: set[str],
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for field_name, field_info in node_cls.model_fields.items():
        if field_name in provided_fields:
            continue
        default = field_info.default
        if default is PydanticUndefined:
            continue
        placeholders = _collect_credential_placeholders(default)
        for placeholder in placeholders:
            entries.append(
                {
                    "node_type": class_name,
                    "field": field_name,
                    "placeholder": placeholder,
                }
            )
    return entries


def _collect_credential_placeholders(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if _is_credential_placeholder(value) else []
    if isinstance(value, dict):
        placeholders: list[str] = []
        for nested in value.values():
            placeholders.extend(_collect_credential_placeholders(nested))
        return placeholders
    if isinstance(value, list | tuple | set):
        placeholders = []
        for nested in value:
            placeholders.extend(_collect_credential_placeholders(nested))
        return placeholders
    return []


def _is_credential_placeholder(value: str) -> bool:
    match = _CREDENTIAL_PLACEHOLDER_PATTERN.fullmatch(value.strip())
    if match is None:
        return False
    body = match.group("body").strip()
    if not body:
        return False
    identifier = body.split("#", 1)[0].strip()
    return bool(identifier)


@lru_cache(maxsize=256)
def _resolve_orcheo_node_class(
    class_name: str,
    module_name: str | None,
) -> type[BaseModel] | None:
    candidates = []
    if module_name is not None:
        candidates.append((module_name, class_name))
    candidates.append((_ORCHEO_NODE_MODULE_PREFIX, class_name))

    for candidate_module, candidate_name in candidates:
        if not candidate_module.startswith(_ORCHEO_NODE_MODULE_PREFIX):
            continue
        try:
            module = importlib.import_module(candidate_module)
        except Exception:
            continue
        node_cls = getattr(module, candidate_name, None)
        if isinstance(node_cls, type) and issubclass(node_cls, BaseModel):
            return node_cls
    return None


def _get_call_module(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Attribute):
        return None
    parts: list[str] = []
    current: ast.expr = node.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    if not parts:
        return None
    return ".".join(reversed(parts))


def extract_graph_index(source: str) -> dict[str, Any]:
    """Parse a LangGraph script and extract cron and listener metadata via AST walk.

    Returns a dict with ``cron`` and ``listeners`` keys. Falls back to empty
    lists if the source cannot be parsed (syntax errors are caught separately
    by the RP compile step).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"cron": [], "listeners": [], "credentials": []}

    visitor = _AddNodeVisitor()
    visitor.visit(tree)
    return {
        "cron": visitor.cron_entries,
        "listeners": visitor.listener_entries,
        "credentials": visitor.credential_entries,
    }


__all__ = ["extract_graph_index"]
