"""Tests for AST-based metadata extraction in ast_extraction.py."""

from __future__ import annotations
import ast
import textwrap

import pytest

from orcheo.graph.ingestion.ast_extraction import (
    _AddNodeVisitor,
    _extract_kwargs,
    _get_call_name,
    _literal_value,
    extract_graph_index,
)


# ---------------------------------------------------------------------------
# _literal_value
# ---------------------------------------------------------------------------


def test_literal_value_constant_int() -> None:
    node = ast.Constant(value=42)
    assert _literal_value(node) == 42


def test_literal_value_constant_string() -> None:
    node = ast.Constant(value="hello")
    assert _literal_value(node) == "hello"


def test_literal_value_constant_bool() -> None:
    node = ast.Constant(value=False)
    assert _literal_value(node) is False


def test_literal_value_unary_minus_int() -> None:
    inner = ast.Constant(value=3)
    node = ast.UnaryOp(op=ast.USub(), operand=inner)
    assert _literal_value(node) == -3


def test_literal_value_unary_minus_float() -> None:
    inner = ast.Constant(value=1.5)
    node = ast.UnaryOp(op=ast.USub(), operand=inner)
    assert _literal_value(node) == -1.5


def test_literal_value_unary_minus_non_numeric() -> None:
    """USub applied to a non-numeric constant returns None."""
    inner = ast.Constant(value="text")
    node = ast.UnaryOp(op=ast.USub(), operand=inner)
    assert _literal_value(node) is None


def test_literal_value_list_literal() -> None:
    node = ast.parse("[1, 2, 3]", mode="eval").body
    assert _literal_value(node) == [1, 2, 3]


def test_literal_value_dict_literal() -> None:
    node = ast.parse("{'a': 1}", mode="eval").body
    assert _literal_value(node) == {"a": 1}


def test_literal_value_non_literal_expression() -> None:
    """A complex expression that cannot be statically evaluated returns None."""
    node = ast.parse("x + 1", mode="eval").body
    assert _literal_value(node) is None


# ---------------------------------------------------------------------------
# _extract_kwargs
# ---------------------------------------------------------------------------


def _build_call(kwargs: dict) -> ast.Call:
    """Helper to construct a synthetic ast.Call with keyword args."""
    keywords = []
    for key, val in kwargs.items():
        kw = ast.keyword(arg=key, value=ast.Constant(value=val))
        keywords.append(kw)
    return ast.Call(func=ast.Name(id="Foo", ctx=ast.Load()), args=[], keywords=keywords)


def test_extract_kwargs_basic() -> None:
    call = _build_call({"expr": "*/5 * * * *", "timezone": "UTC"})
    result = _extract_kwargs(call)
    assert result == {"expr": "*/5 * * * *", "timezone": "UTC"}


def test_extract_kwargs_skips_none_arg() -> None:
    """A keyword with arg=None (i.e. **kwargs spread) is skipped."""
    kw_spread = ast.keyword(arg=None, value=ast.Constant(value="ignored"))
    kw_normal = ast.keyword(arg="key", value=ast.Constant(value="val"))
    call = ast.Call(
        func=ast.Name(id="F", ctx=ast.Load()),
        args=[],
        keywords=[kw_spread, kw_normal],
    )
    result = _extract_kwargs(call)
    assert result == {"key": "val"}


def test_extract_kwargs_skips_non_literal_value() -> None:
    """Non-literal values (e.g. variable references) are omitted from the result."""
    kw = ast.keyword(arg="ref", value=ast.Name(id="some_var", ctx=ast.Load()))
    call = ast.Call(func=ast.Name(id="F", ctx=ast.Load()), args=[], keywords=[kw])
    result = _extract_kwargs(call)
    assert result == {}


def test_extract_kwargs_empty() -> None:
    call = _build_call({})
    result = _extract_kwargs(call)
    assert result == {}


# ---------------------------------------------------------------------------
# _get_call_name
# ---------------------------------------------------------------------------


def test_get_call_name_name_node() -> None:
    node = ast.Name(id="CronTriggerNode", ctx=ast.Load())
    assert _get_call_name(node) == "CronTriggerNode"


def test_get_call_name_attribute_node() -> None:
    node = ast.Attribute(
        value=ast.Name(id="module", ctx=ast.Load()),
        attr="MyNode",
        ctx=ast.Load(),
    )
    assert _get_call_name(node) == "MyNode"


def test_get_call_name_other_returns_none() -> None:
    node = ast.Constant(value=42)
    assert _get_call_name(node) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _AddNodeVisitor
# ---------------------------------------------------------------------------


def _parse_and_visit(source: str) -> _AddNodeVisitor:
    tree = ast.parse(textwrap.dedent(source))
    visitor = _AddNodeVisitor()
    visitor.visit(tree)
    return visitor


def test_visitor_visit_expr_non_call() -> None:
    """Expr nodes that are not Call nodes should not crash the visitor."""
    visitor = _parse_and_visit("x = 1\n1 + 2\n")
    assert visitor.cron_entries == []
    assert visitor.listener_entries == []


def test_visitor_handle_call_non_attribute_func() -> None:
    """A call where func is not Attribute (e.g. bare name) is ignored."""
    visitor = _parse_and_visit("add_node('n', Foo())\n")
    assert visitor.cron_entries == []
    assert visitor.listener_entries == []


def test_visitor_handle_call_wrong_method_name() -> None:
    """A call on a method other than add_node is ignored."""
    visitor = _parse_and_visit("graph.remove_node('n', Foo())\n")
    assert visitor.cron_entries == []
    assert visitor.listener_entries == []


def test_visitor_handle_call_too_few_args() -> None:
    """A call with fewer than 2 positional arguments is ignored."""
    visitor = _parse_and_visit("graph.add_node('name')\n")
    assert visitor.cron_entries == []
    assert visitor.listener_entries == []


def test_visitor_handle_call_non_string_name_arg() -> None:
    """If the first arg is not a string constant, the call is ignored."""
    visitor = _parse_and_visit("graph.add_node(some_var, Foo())\n")
    assert visitor.cron_entries == []
    assert visitor.listener_entries == []


def test_visitor_handle_call_non_call_ctor_arg() -> None:
    """If the second arg is not a Call node, the call is ignored."""
    visitor = _parse_and_visit("graph.add_node('node', some_var)\n")
    assert visitor.cron_entries == []
    assert visitor.listener_entries == []


def test_visitor_handle_call_ctor_with_unknown_callable_arg() -> None:
    """A nested Call without a resolvable name is ignored."""
    visitor = _parse_and_visit("graph.add_node('node', factory()())\n")
    assert visitor.cron_entries == []
    assert visitor.listener_entries == []


def test_visitor_cron_node_with_fields() -> None:
    source = """\
        graph.add_node(
            "cron_trigger",
            CronTriggerNode(
                expression="*/5 * * * *",
                timezone="UTC",
                allow_overlapping=False,
            ),
        )
    """
    visitor = _parse_and_visit(source)
    assert len(visitor.cron_entries) == 1
    entry = visitor.cron_entries[0]
    assert entry["expression"] == "*/5 * * * *"
    assert entry["timezone"] == "UTC"
    assert entry["allow_overlapping"] is False


def test_visitor_cron_node_skips_empty_entry() -> None:
    """CronTriggerNode with no recognized fields produces no entry."""
    source = """\
        graph.add_node(
            "cron",
            CronTriggerNode(unknown_field=42),
        )
    """
    visitor = _parse_and_visit(source)
    assert visitor.cron_entries == []


def test_visitor_listener_known_platform_class() -> None:
    source = """\
        graph.add_node(
            "tg",
            TelegramBotListenerNode(token="secret"),
        )
    """
    visitor = _parse_and_visit(source)
    assert len(visitor.listener_entries) == 1
    entry = visitor.listener_entries[0]
    assert entry["node_name"] == "tg"
    assert entry["type"] == "TelegramBotListenerNode"
    assert entry["platform"] == "telegram"
    assert entry["token"] == "secret"


def test_visitor_listener_platform_kwarg_overrides_class_map() -> None:
    """An explicit 'platform' kwarg takes precedence over the class→platform map."""
    source = """\
        graph.add_node(
            "custom",
            TelegramBotListenerNode(platform="custom_plat", extra="val"),
        )
    """
    visitor = _parse_and_visit(source)
    assert len(visitor.listener_entries) == 1
    entry = visitor.listener_entries[0]
    assert entry["platform"] == "custom_plat"
    assert entry["extra"] == "val"
    assert "platform" not in {k for k, v in entry.items() if k != "platform"}


def test_visitor_listener_unknown_class_with_listener_suffix() -> None:
    """Classes ending in 'ListenerNode' that aren't in the map get empty platform."""
    source = """\
        graph.add_node(
            "node",
            SlackListenerNode(token="tok"),
        )
    """
    visitor = _parse_and_visit(source)
    assert len(visitor.listener_entries) == 1
    entry = visitor.listener_entries[0]
    assert entry["platform"] == ""
    assert entry["token"] == "tok"


def test_visitor_listener_discord_platform() -> None:
    source = """\
        graph.add_node(
            "discord_node",
            DiscordBotListenerNode(token="disc_token"),
        )
    """
    visitor = _parse_and_visit(source)
    assert len(visitor.listener_entries) == 1
    assert visitor.listener_entries[0]["platform"] == "discord"


# ---------------------------------------------------------------------------
# extract_graph_index
# ---------------------------------------------------------------------------


def test_extract_graph_index_empty_script() -> None:
    result = extract_graph_index("x = 1\n")
    assert result == {"cron": [], "listeners": []}


def test_extract_graph_index_cron_entry() -> None:
    source = textwrap.dedent("""\
        graph.add_node(
            "cron",
            CronTriggerNode(expression="0 * * * *", timezone="UTC"),
        )
    """)
    result = extract_graph_index(source)
    assert len(result["cron"]) == 1
    assert result["cron"][0]["expression"] == "0 * * * *"


def test_extract_graph_index_listener_entry() -> None:
    source = textwrap.dedent("""\
        graph.add_node(
            "tg",
            TelegramBotListenerNode(token="tok"),
        )
    """)
    result = extract_graph_index(source)
    assert len(result["listeners"]) == 1
    assert result["listeners"][0]["platform"] == "telegram"


def test_extract_graph_index_syntax_error_returns_empty() -> None:
    result = extract_graph_index("def broken(:\n    pass")
    assert result == {"cron": [], "listeners": []}
