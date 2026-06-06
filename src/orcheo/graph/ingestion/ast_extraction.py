"""AST-based metadata extraction for LangGraph workflow scripts."""

from __future__ import annotations
import ast
from typing import Any


_CRON_NODE_TYPE = "CronTriggerNode"
_CRON_FIELDS: frozenset[str] = frozenset(
    {"expression", "timezone", "allow_overlapping", "start_at", "end_at"}
)

_LISTENER_NODE_PLATFORMS: dict[str, str] = {
    "TelegramBotListenerNode": "telegram",
    "DiscordBotListenerNode": "discord",
    "QQBotListenerNode": "qq",
}


def _literal_value(node: ast.expr) -> Any:
    """Return the Python literal value of a constant AST node, or None."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        val = _literal_value(node.operand)
        if isinstance(val, (int, float)):
            return -val
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
        self.cron_entries: list[dict[str, Any]] = []
        self.listener_entries: list[dict[str, Any]] = []

    def visit_Expr(self, node: ast.Expr) -> None:  # noqa: N802
        if isinstance(node.value, ast.Call):
            self._handle_call(node.value)
        self.generic_visit(node)

    def _handle_call(self, call: ast.Call) -> None:
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
        class_name = _get_call_name(ctor_node.func)
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
            entry: dict[str, Any] = {
                "node_name": node_name,
                "type": class_name,
                "platform": platform,
            }
            entry.update({k: v for k, v in kwargs.items() if k != "platform"})
            self.listener_entries.append(entry)


def extract_graph_index(source: str) -> dict[str, Any]:
    """Parse a LangGraph script and extract cron and listener metadata via AST walk.

    Returns a dict with ``cron`` and ``listeners`` keys. Falls back to empty
    lists if the source cannot be parsed (syntax errors are caught separately
    by the RP compile step).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"cron": [], "listeners": []}

    visitor = _AddNodeVisitor()
    visitor.visit(tree)
    return {"cron": visitor.cron_entries, "listeners": visitor.listener_entries}


__all__ = ["extract_graph_index"]
