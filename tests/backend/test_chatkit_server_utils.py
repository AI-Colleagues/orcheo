"""Tests for ChatKit server helpers that parse widget payloads and route actions."""

from __future__ import annotations
import builtins
import importlib
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import pytest
from chatkit.errors import CustomStreamError
from chatkit.store import Store
from chatkit.types import (
    AssistantMessageContent,
    AssistantMessageItem,
    NoticeEvent,
    ThreadItemDoneEvent,
    ThreadMetadata,
    WidgetItem,
)
from dynaconf import Dynaconf
from orcheo_backend.app.chatkit import server as server_module
from orcheo_backend.app.chatkit.context import ChatKitRequestContext
from orcheo_backend.app.chatkit.telemetry import chatkit_telemetry
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)


class DummyStore(Store[ChatKitRequestContext]):
    """Minimal store implementation used by the server helpers."""

    def __init__(self) -> None:
        self.generated: list[str] = []
        self.saved: list[str] = []
        self.added: list[tuple[str, Any]] = []

    def generate_item_id(
        self,
        item_type: str,
        thread: ThreadMetadata,
        context: ChatKitRequestContext,
    ) -> str:
        identifier = f"{item_type}-{len(self.generated) + 1}"
        self.generated.append(identifier)
        return identifier

    async def load_thread(self, _, __):
        raise NotImplementedError

    async def save_thread(self, thread_id: str, context: ChatKitRequestContext) -> None:
        self.saved.append(thread_id)

    async def load_thread_items(self, *args, **kwargs):
        raise NotImplementedError

    async def save_attachment(self, *args, **kwargs):
        raise NotImplementedError

    async def load_attachment(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_attachment(self, *args, **kwargs):
        raise NotImplementedError

    async def load_threads(self, *args, **kwargs):
        raise NotImplementedError

    async def add_thread_item(
        self, thread_id: str, item: Any, context: ChatKitRequestContext
    ) -> None:
        self.added.append((thread_id, item))

    async def save_item(self, *args, **kwargs):
        raise NotImplementedError

    async def load_item(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_thread(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_thread_item(self, *args, **kwargs):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def reset_chatkit_metrics() -> None:
    """Reset chatkit telemetry between tests."""
    chatkit_telemetry.reset()
    yield
    chatkit_telemetry.reset()


@pytest.fixture(autouse=True)
def reset_widget_policy_state() -> Iterator[None]:
    """Keep widget policy globals consistent between tests."""
    original_widget_types = set(server_module._WIDGET_TYPES)
    original_action_types = set(server_module._ALLOWED_WIDGET_ACTION_TYPES)
    try:
        yield
    finally:
        server_module._WIDGET_TYPES.clear()
        server_module._WIDGET_TYPES.update(original_widget_types)
        server_module._ALLOWED_WIDGET_ACTION_TYPES.clear()
        server_module._ALLOWED_WIDGET_ACTION_TYPES.update(original_action_types)


def create_server() -> tuple[server_module.OrcheoChatKitServer, DummyStore]:
    store = DummyStore()
    repository = Mock()
    server = server_module.OrcheoChatKitServer(
        store=store,
        repository=repository,
        vault_provider=lambda: None,
    )
    return server, store


def test_coerce_config_set_handles_none_and_unknown_type() -> None:
    default = {"Card", "ListView"}

    assert server_module._coerce_config_set(None, default) == default
    assert server_module._coerce_config_set(123, default) == default


def test_coerce_config_set_parses_strings_and_iterables() -> None:
    default = {"Card", "ListView"}

    assert server_module._coerce_config_set(" custom , ListView ", default) == {
        "custom",
        "ListView",
    }
    assert server_module._coerce_config_set(" , ", default) == default
    assert server_module._coerce_config_set(["Card", "  ", "ListView"], default) == {
        "Card",
        "ListView",
    }
    assert server_module._coerce_config_set(("Widget", " "), default) == {"Widget"}
    assert server_module._coerce_config_set(frozenset({"Card", "Custom"}), default) == {
        "Card",
        "Custom",
    }


def test_action_type_for_logging_handles_mapping_and_attributes() -> None:
    mapping = {"type": "map"}
    assert server_module._action_type_for_logging(mapping) == "map"

    class ActionObject:
        type = "object"

    assert server_module._action_type_for_logging(ActionObject()) == "object"


def test_candidate_type_reads_from_mapping_and_attributes() -> None:
    assert server_module._candidate_type({"type": "Card"}) == "Card"

    class Candidate:
        type = "ListView"

    assert server_module._candidate_type(Candidate()) == "ListView"


def test_content_text_extracts_from_strings_and_lists() -> None:
    assert server_module._content_text("plain") == "plain"

    mixed = [
        {"text": "first"},
        SimpleNamespace(text="second"),
        {"content": "ignored"},
    ]
    assert server_module._content_text(mixed) == "first"
    assert server_module._content_text([SimpleNamespace(nontext=1)]) is None


def test_content_text_uses_attribute_fallback() -> None:
    entries = [
        {"content": "ignored"},
        SimpleNamespace(text="attr text"),
    ]
    assert server_module._content_text(entries) == "attr text"


def test_candidate_from_content_handles_invalid_json() -> None:
    assert server_module._candidate_from_content("not-json", None) is None


def test_candidate_from_content_rejects_non_widget_types() -> None:
    assert server_module._candidate_from_content('{"type": "Unknown"}', "copy") is None


def test_candidate_from_content_builds_widget_candidate() -> None:
    candidate = server_module._candidate_from_content(
        '{"type": "Card"}', copy_text="copy"
    )
    assert candidate is not None
    assert candidate.payload["type"] == "Card"
    assert candidate.copy_text == "copy"


def test_candidate_from_content_rejects_unknown_types() -> None:
    assert server_module._candidate_from_content('{"type": "Unknown"}', None) is None
    assert server_module._candidate_from_content("not json", None) is None


def test_candidate_from_artifact_returns_structured_content() -> None:
    artifact = {
        "structured_content": {"type": "ListView"},
        "copy_text": "copy",
    }
    candidate = server_module._candidate_from_artifact(artifact)
    assert candidate is not None
    assert candidate.payload["type"] == "ListView"
    assert candidate.copy_text == "copy"


def test_extract_widget_candidate_prefers_artifact() -> None:
    message = {
        "artifact": {
            "structured_content": {"type": "Card"},
            "copy_text": "text",
        },
        "content": '{"type": "ListView"}',
    }
    candidate = server_module._extract_widget_candidate(message)
    assert candidate is not None
    assert candidate.payload["type"] == "Card"
    assert candidate.copy_text == "text"


def test_extract_widget_candidate_falls_back_to_content() -> None:
    message = {"content": '{"type": "Card"}'}
    candidate = server_module._extract_widget_candidate(message)
    assert candidate is not None
    assert candidate.payload["type"] == "Card"


def test_extract_widget_candidate_accepts_artifact_without_type() -> None:
    message = {
        "artifact": {
            "structured_content": {},
            "copy_text": "no-type",
        }
    }
    candidate = server_module._extract_widget_candidate(message)
    assert candidate is not None
    assert candidate.payload == {}
    assert candidate.copy_text == "no-type"


def test_validate_widget_root_rejects_missing_type() -> None:
    with pytest.raises(server_module._WidgetHydrationError):
        server_module._validate_widget_root({"content": "no type"})


def test_validate_widget_root_rejects_large_payload() -> None:
    payload = {"type": "Card", "title": "x" * 100_000}
    with pytest.raises(server_module._WidgetHydrationError) as excinfo:
        server_module._validate_widget_root(payload)
    assert excinfo.value.reason == "too_large"


def test_notice_for_widget_error_variants() -> None:
    large_error = server_module._WidgetHydrationError(
        "too_large", detail="big", size_bytes=60_000
    )
    result = server_module._notice_for_widget_error(large_error)
    assert "too large" in result.message

    missing_error = server_module._WidgetHydrationError("invalid_widget")
    notice = server_module._notice_for_widget_error(missing_error)
    assert (
        notice.message == "The workflow returned a widget that could not be rendered."
    )


@pytest.mark.asyncio
async def test_hydrate_widget_items_returns_widget_and_notices() -> None:
    server, _store = create_server()
    thread = ThreadMetadata(
        id="thread",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "workflow"},
    )
    context: ChatKitRequestContext = {}
    message = {"type": "tool", "content": '{"type": "Card"}'}
    widget_items, notices = await server._hydrate_widget_items(
        thread, {"_messages": [message]}, context
    )
    assert len(widget_items) == 1
    assert not notices

    # Invalid payload should produce a notice
    invalid_message = {
        "type": "tool",
        "artifact": {"structured_content": {"foo": "bar"}},
    }
    widget_items, notices = await server._hydrate_widget_items(
        thread, {"_messages": [invalid_message]}, context
    )
    assert widget_items == []
    assert notices


@pytest.mark.asyncio
async def test_hydrate_widget_items_skips_non_tool_messages() -> None:
    server, _store = create_server()
    thread = ThreadMetadata(
        id="thread",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "workflow"},
    )
    context: ChatKitRequestContext = {}
    widget_items, notices = await server._hydrate_widget_items(
        thread, {"_messages": [{"type": "not_tool"}]}, context
    )
    assert widget_items == []
    assert notices == []


@pytest.mark.asyncio
async def test_hydrate_widget_items_records_metrics_on_error() -> None:
    server, _store = create_server()
    thread = ThreadMetadata(
        id="thread-metrics",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "workflow"},
    )
    context: ChatKitRequestContext = {}

    widget_items, notices = await server._hydrate_widget_items(
        thread,
        {
            "_messages": [
                {"type": "tool", "artifact": {"structured_content": {"foo": "bar"}}}
            ]
        },
        context,
    )

    assert widget_items == []
    assert notices
    assert (
        chatkit_telemetry.metrics().get("widget.validation_error.invalid_widget") == 1
    )


def test_workflow_id_from_thread_reads_metadata() -> None:
    thread = ThreadMetadata(
        id="thread",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "id"},
    )
    assert server_module._workflow_id_from_thread(thread) == "id"

    thread.metadata = {}
    assert server_module._workflow_id_from_thread(thread) is None


def test_is_tool_message_recognises_tool_types() -> None:
    assert server_module._is_tool_message({"type": "tool"})

    class ToolLike:
        type = "tool"

    assert not server_module._is_tool_message(ToolLike())
    assert not server_module._is_tool_message({"type": "other"})


def test_is_supported_action_type_returns_notice_and_metrics() -> None:
    server, _store = create_server()
    thread = ThreadMetadata(
        id="thread-action",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf"},
    )

    result = server._is_supported_action_type(thread, {"type": "noop"})

    assert not result.allowed
    assert result.notice is not None
    assert result.reason == "unsupported_widget_action"
    metrics = chatkit_telemetry.metrics()
    assert metrics.get("widget_action.unsupported.noop") == 1


def test_is_supported_action_type_allows_submit() -> None:
    server, _store = create_server()
    thread = ThreadMetadata(
        id="thread-allowed",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf"},
    )

    result = server._is_supported_action_type(thread, {"type": "submit"})

    assert result.allowed
    assert result.notice is None
    assert chatkit_telemetry.metrics() == {}


@pytest.mark.asyncio
async def test_action_skips_processing_when_action_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, _store = create_server()
    thread = ThreadMetadata(
        id="thread-blocked",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf"},
    )

    monkeypatch.setattr(server, "_ensure_workflow_metadata", lambda *_: None)
    monkeypatch.setattr(server, "_require_workflow_id", lambda *_: uuid4())
    blocked = server_module._ActionValidationResult(
        allowed=False,
        notice=None,
        reason="unsupported_widget_action",
        action_type="noop",
    )
    monkeypatch.setattr(server, "_is_supported_action_type", lambda *_: blocked)

    history = AsyncMock()
    monkeypatch.setattr(server, "_history", history)
    run_workflow = AsyncMock()
    monkeypatch.setattr(server, "_run_workflow", run_workflow)

    events: list[Any] = []
    async for event in server.action(thread, {"type": "noop"}, None, {}):
        events.append(event)

    assert events == []
    history.assert_not_awaited()
    run_workflow.assert_not_awaited()


def test_refresh_widget_policy_respects_configuration() -> None:
    original_widget_types = set(server_module._WIDGET_TYPES)
    original_action_types = set(server_module._ALLOWED_WIDGET_ACTION_TYPES)
    settings = Dynaconf(settings_files=[], load_dotenv=False)
    settings.set("CHATKIT_WIDGET_TYPES", ["Custom"])
    settings.set("CHATKIT_WIDGET_ACTION_TYPES", ["tap"])

    server_module._refresh_widget_policy(settings)

    try:
        assert server_module._WIDGET_TYPES == {"Custom"}
        assert server_module._ALLOWED_WIDGET_ACTION_TYPES == {"tap"}
    finally:
        reset_settings = Dynaconf(settings_files=[], load_dotenv=False)
        reset_settings.set("CHATKIT_WIDGET_TYPES", list(original_widget_types))
        reset_settings.set("CHATKIT_WIDGET_ACTION_TYPES", list(original_action_types))
        server_module._refresh_widget_policy(reset_settings)


def test_refresh_widget_policy_respects_mapping_without_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MappingWithoutGet(Mapping[str, list[str]]):
        def __init__(self) -> None:
            self._data = {
                "CHATKIT_WIDGET_TYPES": ["MappedCard"],
                "CHATKIT_WIDGET_ACTION_TYPES": ["mapped"],
            }

        def __getitem__(self, key: str) -> list[str]:
            return self._data[key]

        def __iter__(self):
            return iter(self._data)

        def __len__(self) -> int:
            return len(self._data)

    mapping = MappingWithoutGet()

    original_hasattr = builtins.hasattr

    def fake_hasattr(obj: object, name: str) -> bool:  # type: ignore[override]
        if obj is mapping and name == "get":
            return False
        return original_hasattr(obj, name)

    monkeypatch.setattr(builtins, "hasattr", fake_hasattr)

    server_module._refresh_widget_policy(mapping)

    assert server_module._WIDGET_TYPES == {"MappedCard"}
    assert server_module._ALLOWED_WIDGET_ACTION_TYPES == {"mapped"}


def test_refresh_widget_policy_respects_attribute_config() -> None:
    class AttributeConfig:
        chatkit_widget_types = ["AttributeCard"]
        chatkit_widget_action_types = ["tap"]

    server_module._refresh_widget_policy(AttributeConfig())

    assert server_module._WIDGET_TYPES == {"AttributeCard"}
    assert server_module._ALLOWED_WIDGET_ACTION_TYPES == {"tap"}


def test_server_module_import_does_not_require_vault_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing the server module should stay lazy about settings loading."""

    monkeypatch.delenv("ORCHEO_VAULT_ENCRYPTION_KEY", raising=False)

    reloaded = importlib.reload(server_module)

    assert reloaded._WIDGET_TYPES == {"Card", "ListView"}
    assert reloaded._ALLOWED_WIDGET_ACTION_TYPES == {"submit"}


@pytest.mark.asyncio
async def test_action_handles_workflow_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, store = create_server()
    thread = ThreadMetadata(
        id="thread-not-found",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf"},
    )
    monkeypatch.setattr(server, "_ensure_workflow_metadata", lambda *_: None)
    monkeypatch.setattr(server, "_require_workflow_id", lambda *_: uuid4())
    monkeypatch.setattr(server, "_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        server_module,
        "build_action_inputs_payload",
        lambda *_: {"payload": "value"},
    )
    mock_failure = AsyncMock(side_effect=WorkflowNotFoundError("missing"))
    monkeypatch.setattr(server, "_run_workflow", mock_failure)
    log_mock = Mock()
    server._log_action_failure = log_mock

    with pytest.raises(CustomStreamError):
        async for _ in server.action(thread, {"type": "submit"}, None, {}):
            ...
    assert log_mock.called


@pytest.mark.asyncio
async def test_action_handles_version_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, store = create_server()
    thread = ThreadMetadata(
        id="thread-version",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf"},
    )
    monkeypatch.setattr(server, "_ensure_workflow_metadata", lambda *_: None)
    monkeypatch.setattr(server, "_require_workflow_id", lambda *_: uuid4())
    monkeypatch.setattr(server, "_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        server_module,
        "build_action_inputs_payload",
        lambda *_: {"payload": "value"},
    )
    mock_failure = AsyncMock(side_effect=WorkflowVersionNotFoundError("version"))
    monkeypatch.setattr(server, "_run_workflow", mock_failure)
    server._log_action_failure = Mock()

    with pytest.raises(CustomStreamError):
        async for _ in server.action(thread, {"type": "submit"}, None, {}):
            ...
    assert server._log_action_failure.called


@pytest.mark.asyncio
async def test_action_logs_and_reraises_generic_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, store = create_server()
    thread = ThreadMetadata(
        id="thread-error",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf"},
    )
    monkeypatch.setattr(server, "_ensure_workflow_metadata", lambda *_: None)
    monkeypatch.setattr(server, "_require_workflow_id", lambda *_: uuid4())
    monkeypatch.setattr(server, "_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        server_module,
        "build_action_inputs_payload",
        lambda *_: {"payload": "value"},
    )
    mock_failure = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(server, "_run_workflow", mock_failure)
    log_mock = Mock()
    server._log_action_failure = log_mock

    with pytest.raises(RuntimeError):
        async for _ in server.action(thread, {"type": "submit"}, None, {}):
            ...
    assert log_mock.called


# ---------------------------------------------------------------------------
# _record_hydrated_widget_tool_calls (server.py lines 207, 212->211)
# ---------------------------------------------------------------------------


def test_record_hydrated_widget_tool_calls_appends_to_existing_list() -> None:
    """Covers line 207: existing metadata already has the key set to a list."""
    thread = ThreadMetadata(
        id="t207",
        created_at=datetime.now(UTC),
        metadata={server_module._HYDRATED_WIDGET_TOOL_CALLS_KEY: ["old-id"]},
    )
    server_module._record_hydrated_widget_tool_calls(thread, {"new-id"})
    result = thread.metadata[server_module._HYDRATED_WIDGET_TOOL_CALLS_KEY]
    assert "old-id" in result
    assert "new-id" in result


def test_record_hydrated_widget_tool_calls_skips_known_ids() -> None:
    """Covers line 212->211: duplicate IDs are not appended again."""
    thread = ThreadMetadata(
        id="t212",
        created_at=datetime.now(UTC),
        metadata={server_module._HYDRATED_WIDGET_TOOL_CALLS_KEY: ["dup-id"]},
    )
    server_module._record_hydrated_widget_tool_calls(thread, {"dup-id"})
    result = thread.metadata[server_module._HYDRATED_WIDGET_TOOL_CALLS_KEY]
    assert result.count("dup-id") == 1


# ---------------------------------------------------------------------------
# _extract_widget_candidate (server.py line 402)
# ---------------------------------------------------------------------------


def test_extract_widget_candidate_returns_none_when_both_candidates_fail() -> None:
    """Covers line 402: no artifact and content type not in _WIDGET_TYPES."""
    message = {
        "type": "tool",
        "content": '{"type": "UnknownType"}',
        "tool_call_id": "tc-402",
    }
    result = server_module._extract_widget_candidate(message)
    assert result is None


# ---------------------------------------------------------------------------
# _action_payload_mapping (server.py line 469)
# ---------------------------------------------------------------------------


def test_action_payload_mapping_uses_getattr_for_non_mapping_action() -> None:
    """Covers line 469: non-Mapping action → getattr(action, 'payload', None)."""

    class AttrAction:
        payload = {"submitted": True}

    result = server_module._action_payload_mapping(AttrAction())
    assert result == {"submitted": True}


# ---------------------------------------------------------------------------
# _resolve_form_value (server.py line 481)
# ---------------------------------------------------------------------------


def test_resolve_form_value_returns_direct_key_match() -> None:
    """Covers line 481: name is a top-level key in the payload."""
    found, value = server_module._resolve_form_value({"opt1": "yes"}, "opt1")
    assert found is True
    assert value == "yes"


# ---------------------------------------------------------------------------
# _coerce_checked (server.py lines 495-499)
# ---------------------------------------------------------------------------


def test_coerce_checked_handles_string_values() -> None:
    """Covers lines 495-496: string branch."""
    assert server_module._coerce_checked("true") is True
    assert server_module._coerce_checked("on") is True
    assert server_module._coerce_checked("1") is True
    assert server_module._coerce_checked("false") is False
    assert server_module._coerce_checked("no") is False


def test_coerce_checked_handles_int_and_float() -> None:
    """Covers lines 497-498: int/float branch."""
    assert server_module._coerce_checked(1) is True
    assert server_module._coerce_checked(0) is False
    assert server_module._coerce_checked(2.5) is True
    assert server_module._coerce_checked(0.0) is False


def test_coerce_checked_handles_other_types() -> None:
    """Covers line 499: fallback ``return value is not None``."""
    assert server_module._coerce_checked(None) is False
    assert server_module._coerce_checked(object()) is True


# ---------------------------------------------------------------------------
# _collect_form_input_nodes (server.py line 509)
# ---------------------------------------------------------------------------


def test_collect_form_input_nodes_returns_early_for_non_mapping_non_list() -> None:
    """Covers line 509: non-Mapping, non-list input returns immediately."""
    names: list[tuple[str, str]] = []
    server_module._collect_form_input_nodes("plain-string", names)
    assert names == []
    server_module._collect_form_input_nodes(42, names)
    assert names == []


# ---------------------------------------------------------------------------
# _apply_submitted_form_values (server.py lines 542, 556-559, 560->564)
# ---------------------------------------------------------------------------


def test_apply_submitted_form_values_non_mapping_non_list_passthrough() -> None:
    """Covers line 542: non-Mapping, non-list node is returned unchanged."""
    result, changed = server_module._apply_submitted_form_values("raw_node", {})
    assert result == "raw_node"
    assert changed is False


def test_apply_submitted_form_values_non_checkbox_found_string() -> None:
    """Covers lines 556-557: ``elif found`` with a str value."""
    node = {"type": "Select", "name": "color", "defaultValue": "red"}
    result, changed = server_module._apply_submitted_form_values(
        node, {"color": "blue"}
    )
    assert result["defaultValue"] == "blue"
    assert changed is True


def test_apply_submitted_form_values_non_checkbox_found_non_string() -> None:
    """Covers line 557 coercion: ``elif found`` with non-str value coerced to str."""
    node = {"type": "Input", "name": "count", "defaultValue": "0"}
    result, changed = server_module._apply_submitted_form_values(node, {"count": 99})
    assert result["defaultValue"] == "99"
    assert changed is True


def test_apply_submitted_form_values_non_checkbox_field_absent_in_payload() -> None:
    """Covers lines 558-559 and 560->564: ``else`` branch keeps existing default."""
    node = {"type": "Select", "name": "color", "defaultValue": "red"}
    result, changed = server_module._apply_submitted_form_values(node, {"other": "val"})
    assert result["defaultValue"] == "red"
    assert changed is False


def test_apply_submitted_form_values_no_change_when_value_already_matches() -> None:
    """Covers line 560->564: submitted value equals existing default → no change."""
    node = {"type": "Select", "name": "size", "defaultValue": "large"}
    result, changed = server_module._apply_submitted_form_values(
        node, {"size": "large"}
    )
    assert result["defaultValue"] == "large"
    assert changed is False


@pytest.mark.asyncio
async def test_action_streams_widgets_and_assistant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, store = create_server()
    thread = ThreadMetadata(
        id="thread-stream",
        created_at=datetime.now(UTC),
        metadata={"workflow_id": "wf"},
    )
    context: ChatKitRequestContext = {
        "actor": "actor",
        "workspace_id": "ws1",
        "upload_session_id": "ups1",
    }
    monkeypatch.setattr(server, "_ensure_workflow_metadata", lambda *_: None)
    monkeypatch.setattr(server, "_require_workflow_id", lambda *_: uuid4())
    monkeypatch.setattr(server, "_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        server_module,
        "build_action_inputs_payload",
        lambda *_: {"payload": "value"},
    )

    run_workflow_mock = AsyncMock(return_value=("reply", {}, None))
    monkeypatch.setattr(server, "_run_workflow", run_workflow_mock)
    notice = NoticeEvent(level="info", message="note")
    widget_item = WidgetItem(
        id="widget",
        thread_id="thread-stream",
        created_at=datetime.now(UTC),
        widget={"type": "Card"},
    )

    async def fake_hydrate(*_: Any):
        return [widget_item], [notice]

    monkeypatch.setattr(server, "_hydrate_widget_items", fake_hydrate)
    assistant_item = AssistantMessageItem(
        id="assistant",
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        content=[AssistantMessageContent(text="reply")],
    )
    monkeypatch.setattr(server, "_build_assistant_item", lambda *_: assistant_item)
    store.add_thread_item = AsyncMock()
    store.save_thread = AsyncMock()
    events: list[Any] = []
    async for event in server.action(thread, {"type": "submit"}, None, context):
        events.append(event)

    assert events[0] is notice
    assert isinstance(events[1], ThreadItemDoneEvent)
    assert events[1].item is widget_item
    assert isinstance(events[2], ThreadItemDoneEvent)
    store.add_thread_item.assert_any_await(thread.id, widget_item, context)
    store.add_thread_item.assert_any_await(thread.id, assistant_item, context)
    store.save_thread.assert_awaited_once_with(thread, context)
    assert run_workflow_mock.await_args.kwargs["workspace_id"] == "ws1"
    assert run_workflow_mock.await_args.kwargs["thread_id"] == "thread-stream"
    assert run_workflow_mock.await_args.kwargs["upload_session_id"] == "ups1"
