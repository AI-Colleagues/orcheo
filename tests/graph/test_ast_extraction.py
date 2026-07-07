"""Tests for AST-based metadata extraction in ast_extraction.py."""

from __future__ import annotations
import ast
import textwrap
import types

import pytest
from pydantic import BaseModel

import orcheo.graph.ingestion.ast_extraction as ast_extraction
from orcheo.graph.ingestion.ast_extraction import (
    _AddNodeVisitor,
    _collect_model_default_credentials,
    _collect_credential_placeholders,
    _extract_kwargs,
    _extract_provided_keyword_fields,
    _get_call_name,
    _get_call_module,
    _is_credential_placeholder,
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


def test_extract_provided_keyword_fields_includes_non_literals() -> None:
    call = ast.parse(
        'MessageTelegram(token=token_from_config, message="hello", **extra)',
        mode="eval",
    ).body
    assert isinstance(call, ast.Call)

    assert _extract_provided_keyword_fields(call) == {"message", "token"}


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


def test_is_credential_placeholder_matches_runtime_shape() -> None:
    assert _is_credential_placeholder("[[credential-name]]")
    assert _is_credential_placeholder("[[credential-name#oauth.access_token]]")
    assert not _is_credential_placeholder("[[  ]]")
    assert not _is_credential_placeholder("[[#oauth.access_token]]")
    assert not _is_credential_placeholder("prefix [[credential-name]]")


class _SyntheticCredentialNode(BaseModel):
    api_key: str = "[[custom-service-key]]"
    nested: dict[str, str] = {"refresh": "[[custom-refresh#oauth.refresh_token]]"}
    normal_value: str = "not-a-credential"


class _RequiredCredentialNode(BaseModel):
    required_value: str
    secret_value: str = "[[required-secret]]"


def test_collect_model_default_credentials_discovers_placeholder_defaults() -> None:
    entries = _collect_model_default_credentials(
        "SyntheticCredentialNode",
        _SyntheticCredentialNode,
        {"api_key"},
    )

    assert entries == [
        {
            "node_type": "SyntheticCredentialNode",
            "field": "nested",
            "placeholder": "[[custom-refresh#oauth.refresh_token]]",
        }
    ]


def test_collect_model_default_credentials_skips_required_fields() -> None:
    entries = _collect_model_default_credentials(
        "RequiredCredentialNode",
        _RequiredCredentialNode,
        set(),
    )

    assert entries == [
        {
            "node_type": "RequiredCredentialNode",
            "field": "secret_value",
            "placeholder": "[[required-secret]]",
        }
    ]


def test_collect_credential_placeholders_returns_empty_for_scalars() -> None:
    assert _collect_credential_placeholders(123) == []


def test_collect_credential_placeholders_finds_embedded_string_placeholders() -> None:
    assert _collect_credential_placeholders("Bearer [[service-token]] suffix") == [
        "[[service-token]]"
    ]


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
    assert visitor.credential_entries == []


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


def test_visitor_add_node_unknown_constructor_type_is_ignored() -> None:
    source = """\
        graph.add_node(
            "plain_node",
            PlainNode(payload="value"),
        )
    """
    visitor = _parse_and_visit(source)
    assert visitor.cron_entries == []
    assert visitor.listener_entries == []


def test_visitor_importfrom_star_and_relative_imports() -> None:
    source = """\
        from . import local_name
        from some.pkg import *
    """
    visitor = _parse_and_visit(source)
    assert visitor.import_aliases == {"local_name": "local_name"}
    assert visitor.import_modules == {}


def test_visitor_import_records_module_aliases() -> None:
    source = """\
        import orcheo.nodes.wecom as wecom
        import orcheo.nodes.storage.mongodb.search
    """
    visitor = _parse_and_visit(source)
    assert visitor.module_aliases["wecom"] == "orcheo.nodes.wecom"
    assert visitor.module_aliases["orcheo"] == "orcheo.nodes.storage.mongodb.search"


def test_resolve_module_alias_without_remainder() -> None:
    visitor = _AddNodeVisitor()
    visitor.module_aliases["wecom"] = "orcheo.nodes.wecom"

    assert visitor._resolve_module_alias("wecom") == "orcheo.nodes.wecom"


def test_handle_constructor_call_uses_import_modules_for_bare_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ResolvedNode(BaseModel):
        connection_string: str = "[[resolved-connection-string]]"

    visitor = _AddNodeVisitor()
    visitor.import_modules["MongoDBEnsureSearchIndexNode"] = (
        "orcheo.nodes.storage.mongodb.search"
    )
    monkeypatch.setattr(
        ast_extraction,
        "_resolve_orcheo_node_class",
        lambda *_args, **_kwargs: _ResolvedNode,
    )

    call = ast.parse(
        'MongoDBEnsureSearchIndexNode(name="ensure_text_index")',
        mode="eval",
    ).body
    assert isinstance(call, ast.Call)

    visitor._handle_constructor_call(call)

    assert visitor.credential_entries == [
        {
            "node_type": "MongoDBEnsureSearchIndexNode",
            "field": "connection_string",
            "placeholder": "[[resolved-connection-string]]",
        }
    ]


def test_handle_constructor_call_resolves_module_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ResolvedNode(BaseModel):
        webhook_key: str = "[[resolved-webhook-key]]"

    visitor = _AddNodeVisitor()
    visitor.module_aliases["wecom"] = "orcheo.nodes.wecom"
    monkeypatch.setattr(
        ast_extraction,
        "_resolve_orcheo_node_class",
        lambda *_args, **_kwargs: _ResolvedNode,
    )

    call = ast.parse('wecom.WeComGroupPushNode(name="push")', mode="eval").body
    assert isinstance(call, ast.Call)

    visitor._handle_constructor_call(call)

    assert visitor.credential_entries == [
        {
            "node_type": "WeComGroupPushNode",
            "field": "webhook_key",
            "placeholder": "[[resolved-webhook-key]]",
        }
    ]


def test_handle_constructor_call_skips_import_module_lookup_when_name_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visitor = _AddNodeVisitor()
    call = ast.parse("ignored()", mode="eval").body
    assert isinstance(call, ast.Call)

    monkeypatch.setattr(visitor, "_resolve_class_name", lambda _node: "SyntheticNode")
    monkeypatch.setattr(ast_extraction, "_get_call_module", lambda _node: None)
    monkeypatch.setattr(ast_extraction, "_get_call_name", lambda _node: None)

    visitor._handle_constructor_call(call)

    assert visitor.credential_entries == []


def test_collect_credential_placeholders_recurses_nested_iterables() -> None:
    value = [
        "[[one]]",
        ("[[two#oauth.refresh_token]]", {"[[three]]"}),
        "not-a-placeholder",
    ]

    assert sorted(_collect_credential_placeholders(value)) == [
        "[[one]]",
        "[[three]]",
        "[[two#oauth.refresh_token]]",
    ]


def test_resolve_orcheo_node_class_falls_back_after_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ast_extraction._resolve_orcheo_node_class.cache_clear()
    fake_module = types.ModuleType("orcheo.nodes")

    class FakeTelegramBotListenerNode(BaseModel):
        pass

    fake_module.TelegramBotListenerNode = FakeTelegramBotListenerNode

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "orcheo.nodes.storage.missing":
            raise ImportError("boom")
        if name == "orcheo.nodes":
            return fake_module
        raise AssertionError(f"unexpected import: {name!r}")

    monkeypatch.setattr(ast_extraction.importlib, "import_module", fake_import_module)

    try:
        node_cls = ast_extraction._resolve_orcheo_node_class(
            "TelegramBotListenerNode",
            "orcheo.nodes.storage.missing",
        )
        assert node_cls is not None
        assert node_cls is FakeTelegramBotListenerNode
    finally:
        ast_extraction._resolve_orcheo_node_class.cache_clear()


def test_resolve_orcheo_node_class_falls_back_when_primary_missing_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ast_extraction._resolve_orcheo_node_class.cache_clear()
    primary_module = types.ModuleType("orcheo.nodes.storage.missing")
    fallback_module = types.ModuleType("orcheo.nodes")

    class FallbackTelegramBotListenerNode(BaseModel):
        pass

    fallback_module.TelegramBotListenerNode = FallbackTelegramBotListenerNode

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "orcheo.nodes.storage.missing":
            return primary_module
        if name == "orcheo.nodes":
            return fallback_module
        raise AssertionError(f"unexpected import: {name!r}")

    monkeypatch.setattr(ast_extraction.importlib, "import_module", fake_import_module)

    try:
        node_cls = ast_extraction._resolve_orcheo_node_class(
            "TelegramBotListenerNode",
            "orcheo.nodes.storage.missing",
        )
        assert node_cls is FallbackTelegramBotListenerNode
    finally:
        ast_extraction._resolve_orcheo_node_class.cache_clear()


def test_resolve_orcheo_node_class_returns_imported_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ast_extraction._resolve_orcheo_node_class.cache_clear()
    fake_module = types.ModuleType("orcheo.nodes.wecom")

    class FakeWeComGroupPushNode(BaseModel):
        pass

    fake_module.WeComGroupPushNode = FakeWeComGroupPushNode

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "orcheo.nodes.wecom":
            return fake_module
        raise AssertionError(f"unexpected import: {name!r}")

    monkeypatch.setattr(ast_extraction.importlib, "import_module", fake_import_module)

    try:
        node_cls = ast_extraction._resolve_orcheo_node_class(
            "WeComGroupPushNode",
            "orcheo.nodes.wecom",
        )
        assert node_cls is not None
        assert node_cls is FakeWeComGroupPushNode
    finally:
        ast_extraction._resolve_orcheo_node_class.cache_clear()


def test_get_call_module_nested_attribute_and_invalid_root() -> None:
    nested = ast.parse("pkg.mod.Class()", mode="eval").body.func
    assert _get_call_module(nested) == "pkg.mod"

    invalid = ast.Attribute(
        value=ast.Constant(value=1),
        attr="Class",
        ctx=ast.Load(),
    )
    assert _get_call_module(invalid) is None


# ---------------------------------------------------------------------------
# extract_graph_index
# ---------------------------------------------------------------------------


def test_extract_graph_index_empty_script() -> None:
    result = extract_graph_index("x = 1\n")
    assert result == {"cron": [], "listeners": [], "credentials": []}


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


def test_extract_graph_index_node_default_credentials() -> None:
    class _ResolvedNode(BaseModel):
        connection_string: str = "[[resolved-connection-string]]"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        ast_extraction,
        "_resolve_orcheo_node_class",
        lambda *_args, **_kwargs: _ResolvedNode,
    )
    try:
        source = textwrap.dedent("""\
            from orcheo.nodes.storage.mongodb.search import MongoDBEnsureSearchIndexNode

            MongoDBEnsureSearchIndexNode(name="ensure_text_index")
        """)

        assert extract_graph_index(source)["credentials"] == [
            {
                "node_type": "MongoDBEnsureSearchIndexNode",
                "field": "connection_string",
                "placeholder": "[[resolved-connection-string]]",
            }
        ]
    finally:
        monkeypatch.undo()


def test_extract_graph_index_literal_constructor_credentials() -> None:
    class _ResolvedNode(BaseModel):
        name: str

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        ast_extraction,
        "_resolve_orcheo_node_class",
        lambda *_args, **_kwargs: _ResolvedNode,
    )
    try:
        source = textwrap.dedent("""\
            AgentNode(
                name="agent",
                model_kwargs={
                    "api_key": "[[openai_api_key]]",
                    "authorization": "Bearer [[secondary_token]]",
                },
            )
        """)

        result = extract_graph_index(source)

        assert result["credentials"] == [
            {
                "node_type": "AgentNode",
                "field": "model_kwargs",
                "placeholder": "[[openai_api_key]]",
            },
            {
                "node_type": "AgentNode",
                "field": "model_kwargs",
                "placeholder": "[[secondary_token]]",
            },
        ]
    finally:
        monkeypatch.undo()


def test_extract_graph_index_skips_overridden_default_credential() -> None:
    source = textwrap.dedent("""\
        from orcheo.nodes.storage.mongodb import MongoDBEnsureSearchIndexNode as Search

        Search(
            name="ensure_text_index",
            connection_string="{{config.configurable.connection_string}}",
            database="{{config.configurable.database}}",
            collection="{{config.configurable.collection}}",
            definition={"mappings": {"dynamic": False}},
        )
    """)

    result = extract_graph_index(source)

    assert result["credentials"] == []


def test_extract_graph_index_skips_non_literal_overridden_default_credential() -> None:
    class _ResolvedNode(BaseModel):
        name: str
        token: str = "[[telegram_token]]"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        ast_extraction,
        "_resolve_orcheo_node_class",
        lambda *_args, **_kwargs: _ResolvedNode,
    )
    try:
        source = textwrap.dedent("""\
            from orcheo.nodes.connectors.telegram import MessageTelegram

            token_from_config = "{{config.configurable.telegram_token}}"

            MessageTelegram(
                name="send",
                token=token_from_config,
                message="{{inputs.message}}",
            )
        """)

        result = extract_graph_index(source)

        assert result["credentials"] == []
    finally:
        monkeypatch.undo()


def test_extract_graph_index_does_not_fallback_for_custom_import_collision() -> None:
    source = textwrap.dedent("""\
        from custom_nodes import PostgresNode

        PostgresNode(name="custom_postgres")
    """)

    result = extract_graph_index(source)

    assert result["credentials"] == []


def test_extract_graph_index_resolves_orcheo_module_alias_default_credential() -> None:
    class _ResolvedNode(BaseModel):
        webhook_key: str = "[[resolved-webhook-key]]"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        ast_extraction,
        "_resolve_orcheo_node_class",
        lambda *_args, **_kwargs: _ResolvedNode,
    )
    try:
        source = textwrap.dedent("""\
            import orcheo.nodes.wecom as wecom

            wecom.WeComGroupPushNode(name="push")
        """)

        assert extract_graph_index(source)["credentials"] == [
            {
                "node_type": "WeComGroupPushNode",
                "field": "webhook_key",
                "placeholder": "[[resolved-webhook-key]]",
            }
        ]
    finally:
        monkeypatch.undo()


def test_extract_graph_index_syntax_error_returns_empty() -> None:
    result = extract_graph_index("def broken(:\n    pass")
    assert result == {"cron": [], "listeners": [], "credentials": []}
