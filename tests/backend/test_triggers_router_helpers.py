from __future__ import annotations
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT
from orcheo.models import CredentialHealthStatus, WorkflowVersion
from orcheo.triggers.webhook import WebhookValidationError
from orcheo.vault.oauth import (
    CredentialHealthError,
    CredentialHealthReport,
    CredentialHealthResult,
)
from orcheo_backend.app.errors import WorkspaceQuotaExceededError
from orcheo_backend.app.routers import triggers


def test_parse_webhook_body_preserves_raw_payload_and_parsed_body() -> None:
    """Raw and parsed payloads are preserved when requested."""

    raw_body = b'{"key": "value"}'
    payload, parsed_body = triggers._parse_webhook_body(
        raw_body, preserve_raw_body=True
    )

    assert payload["raw"] == raw_body.decode("utf-8")
    assert payload["parsed"] == {"key": "value"}
    assert parsed_body == {"key": "value"}


def test_maybe_handle_slack_url_verification_returns_challenge_payload() -> None:
    """Slack URL verification requests are answered with the challenge."""

    response = triggers._maybe_handle_slack_url_verification(
        {"type": "url_verification", "challenge": "test-challenge"}
    )

    assert isinstance(response, JSONResponse)
    assert json.loads(response.body) == {"challenge": "test-challenge"}


def test_maybe_handle_slack_url_verification_rejects_missing_challenge() -> None:
    """Missing Slack challenge values raise an HTTPException."""

    with pytest.raises(HTTPException) as exc_info:
        triggers._maybe_handle_slack_url_verification(
            {"type": "url_verification", "challenge": ""}
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Missing Slack challenge value"


def test_parse_webhook_body_returns_raw_when_not_preserving_bad_json() -> None:
    """When not preserving, invalid JSON payloads surface as bytes."""

    raw_body = b"not-json"
    payload, parsed_body = triggers._parse_webhook_body(
        raw_body, preserve_raw_body=False
    )

    assert payload == raw_body
    assert parsed_body is None


def test_parse_webhook_body_preserves_non_mapping_parsed_payload() -> None:
    """Lists or other non-mappings are preserved but not returned as parsed_body."""

    raw_body = b'["value", 123]'
    payload, parsed_body = triggers._parse_webhook_body(
        raw_body, preserve_raw_body=True
    )

    assert payload["raw"] == raw_body.decode("utf-8")
    assert payload["parsed"] == ["value", 123]
    assert parsed_body is None


def test_build_webhook_state_langgraph_dict_adds_defaults() -> None:
    """LangGraph inputs include defaults and runtime config."""

    state = triggers._build_webhook_state(
        {"format": LANGGRAPH_SCRIPT_FORMAT},
        {"hello": "world"},
        {"exec": "run"},
    )

    assert state["hello"] == "world"
    assert state["inputs"]["hello"] == "world"
    assert state["results"] == {}
    assert state["messages"] == []
    assert state["config"] == {"exec": "run"}


def test_build_webhook_state_langgraph_dict_sets_config_when_present() -> None:
    """LangGraph inputs attach runtime config when provided."""

    state = triggers._build_webhook_state(
        {"format": LANGGRAPH_SCRIPT_FORMAT},
        {"key": "value"},
        {"session": "runtime"},
    )

    assert state["config"] == {"session": "runtime"}


def test_build_webhook_state_langgraph_dict_without_runtime_config() -> None:
    """LangGraph inputs include empty config when runtime config is missing."""

    state = triggers._build_webhook_state(
        {"format": LANGGRAPH_SCRIPT_FORMAT},
        {"key": "value"},
        None,
    )

    assert state["config"] == {}
    assert state["inputs"] == {"key": "value"}


def test_build_webhook_state_langgraph_non_mapping_passthrough() -> None:
    """LangGraph non-mapping inputs are passed through."""

    state = triggers._build_webhook_state(
        {"format": LANGGRAPH_SCRIPT_FORMAT}, ["value"], None
    )

    assert state == ["value"]


def test_build_webhook_state_default_shape() -> None:
    """Non-LangGraph workflows use default state shape."""

    state = triggers._build_webhook_state({}, {"key": "value"}, None)

    assert state["inputs"] == {"key": "value"}
    assert state["results"] == {}
    assert state["messages"] == []
    assert state["config"] == {}


def test_extract_immediate_response_returns_content() -> None:
    """Immediate responses are extracted from results."""

    immediate, should_process = triggers._extract_immediate_response(
        {
            "results": {
                "node": {
                    "immediate_response": {"content": "ok"},
                    "should_process": True,
                }
            }
        }
    )

    assert immediate == {"content": "ok"}
    assert should_process is True


def test_extract_immediate_response_handles_missing() -> None:
    """Missing immediate responses return a default tuple."""

    immediate, should_process = triggers._extract_immediate_response({"results": {}})

    assert immediate is None
    assert should_process is False


def test_extract_immediate_response_skips_invalid_entries() -> None:
    """Non-matching result entries are ignored."""

    immediate, should_process = triggers._extract_immediate_response(
        {
            "results": {
                "node": "not-a-dict",
                "other": {"immediate_response": {}},
            }
        }
    )

    assert immediate is None
    assert should_process is False


class _DummyCheckpointer:
    async def __aenter__(self) -> object:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _DummyCompiled:
    def __init__(self, final_state: dict[str, object]) -> None:
        self._final_state = final_state
        self.seen_state: object | None = None
        self.seen_config: object | None = None

    async def ainvoke(self, state: object, config: object) -> dict[str, object]:
        self.seen_state = state
        self.seen_config = config
        return self._final_state


class _DummyGraph:
    def __init__(self, compiled: _DummyCompiled) -> None:
        self._compiled = compiled
        self.seen_store: object | None = None

    def compile(
        self,
        *,
        checkpointer: object | None = None,
        store: object | None = None,
    ) -> _DummyCompiled:
        self.seen_store = store
        del checkpointer, store
        return self._compiled


class _DummyMergedConfig:
    def to_runnable_config(self, execution_id: str) -> dict[str, str]:
        return {"execution_id": execution_id}

    def to_state_config(self, execution_id: str) -> dict[str, str]:
        return {"execution_id": execution_id}


@pytest.mark.asyncio()
async def test_try_immediate_response_returns_json_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Immediate responses can return JSON payloads."""

    final_state = {
        "results": {
            "node": {
                "immediate_response": {
                    "content": {"ok": True},
                    "content_type": "application/json",
                    "status_code": 201,
                },
                "should_process": False,
            }
        }
    }
    compiled = _DummyCompiled(final_state)
    graph = _DummyGraph(compiled)
    monkeypatch.setattr(triggers, "build_graph", lambda graph_config: graph)
    monkeypatch.setattr(
        triggers, "create_checkpointer", lambda settings: _DummyCheckpointer()
    )
    monkeypatch.setattr(
        triggers, "create_graph_store", lambda settings: _DummyCheckpointer()
    )
    monkeypatch.setattr(
        triggers, "merge_runnable_configs", lambda stored, runtime: _DummyMergedConfig()
    )
    monkeypatch.setattr(triggers, "get_settings", lambda: object())

    version = WorkflowVersion(
        workflow_id=uuid4(),
        version=1,
        graph={"format": LANGGRAPH_SCRIPT_FORMAT},
        created_by="tester",
    )

    response, should_queue = await triggers._try_immediate_response(
        version, {"message": "hi"}, vault=object()
    )

    assert isinstance(response, JSONResponse)
    assert json.loads(response.body) == {"ok": True}
    assert response.status_code == 201
    assert should_queue is False
    assert isinstance(compiled.seen_state, dict)
    assert compiled.seen_state["inputs"] == {"message": "hi"}
    assert graph.seen_store is not None


@pytest.mark.asyncio()
async def test_try_immediate_response_returns_json_string_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Immediate responses accept JSON-encoded strings."""

    final_state = {
        "results": {
            "node": {
                "immediate_response": {
                    "content": json.dumps({"ok": True}),
                    "content_type": "application/json",
                    "status_code": 200,
                },
                "should_process": False,
            }
        }
    }
    compiled = _DummyCompiled(final_state)
    monkeypatch.setattr(triggers, "build_graph", lambda graph: _DummyGraph(compiled))
    monkeypatch.setattr(
        triggers, "create_checkpointer", lambda settings: _DummyCheckpointer()
    )
    monkeypatch.setattr(
        triggers, "create_graph_store", lambda settings: _DummyCheckpointer()
    )
    monkeypatch.setattr(
        triggers, "merge_runnable_configs", lambda stored, runtime: _DummyMergedConfig()
    )
    monkeypatch.setattr(triggers, "get_settings", lambda: object())

    version = WorkflowVersion(
        workflow_id=uuid4(),
        version=1,
        graph={"format": LANGGRAPH_SCRIPT_FORMAT},
        created_by="tester",
    )

    response, should_queue = await triggers._try_immediate_response(
        version, {"message": "hi"}, vault=object()
    )

    assert isinstance(response, JSONResponse)
    assert json.loads(response.body) == {"ok": True}
    assert response.status_code == 200
    assert should_queue is False


@pytest.mark.asyncio()
async def test_try_immediate_response_returns_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Immediate responses can return plain-text payloads."""

    final_state = {
        "results": {
            "node": {
                "immediate_response": {"content": "pong"},
                "should_process": True,
            }
        }
    }
    compiled = _DummyCompiled(final_state)
    monkeypatch.setattr(triggers, "build_graph", lambda graph: _DummyGraph(compiled))
    monkeypatch.setattr(
        triggers, "create_checkpointer", lambda settings: _DummyCheckpointer()
    )
    monkeypatch.setattr(
        triggers, "create_graph_store", lambda settings: _DummyCheckpointer()
    )
    monkeypatch.setattr(
        triggers, "merge_runnable_configs", lambda stored, runtime: _DummyMergedConfig()
    )
    monkeypatch.setattr(triggers, "get_settings", lambda: object())

    version = WorkflowVersion(
        workflow_id=uuid4(),
        version=1,
        graph={"format": LANGGRAPH_SCRIPT_FORMAT},
        created_by="tester",
    )

    response, should_queue = await triggers._try_immediate_response(
        version, {"message": "ping"}, vault=object()
    )

    assert isinstance(response, PlainTextResponse)
    assert response.body == b"pong"
    assert should_queue is True


@pytest.mark.asyncio()
async def test_try_immediate_response_returns_none_when_no_immediate_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no immediate response is found, async processing is queued."""

    final_state = {"results": {"node": {"status": "ok"}}}
    compiled = _DummyCompiled(final_state)
    monkeypatch.setattr(triggers, "build_graph", lambda graph: _DummyGraph(compiled))
    monkeypatch.setattr(
        triggers, "create_checkpointer", lambda settings: _DummyCheckpointer()
    )
    monkeypatch.setattr(
        triggers, "create_graph_store", lambda settings: _DummyCheckpointer()
    )
    monkeypatch.setattr(
        triggers, "merge_runnable_configs", lambda stored, runtime: _DummyMergedConfig()
    )
    monkeypatch.setattr(triggers, "get_settings", lambda: object())

    version = WorkflowVersion(
        workflow_id=uuid4(),
        version=1,
        graph={"format": LANGGRAPH_SCRIPT_FORMAT},
        created_by="tester",
    )

    response, should_queue = await triggers._try_immediate_response(
        version, {"message": "hi"}, vault=object()
    )

    assert response is None
    assert should_queue is True


@pytest.mark.asyncio()
async def test_try_immediate_response_returns_none_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Errors in the immediate response path fall back to async processing."""

    def _raise_error(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(triggers, "build_graph", _raise_error)
    monkeypatch.setattr(triggers, "get_settings", lambda: object())

    version = WorkflowVersion(
        workflow_id=uuid4(),
        version=1,
        graph={"format": LANGGRAPH_SCRIPT_FORMAT},
        created_by="tester",
    )

    response, should_queue = await triggers._try_immediate_response(
        version, {"message": "hi"}, vault=object()
    )

    assert response is None
    assert should_queue is True


def test_build_json_immediate_response_invalid_json_string() -> None:
    """Invalid JSON strings return a raw Response with application/json media type."""
    from fastapi import Response

    result = triggers._build_json_immediate_response("not valid json", 200)

    assert isinstance(result, Response)
    assert not isinstance(result, JSONResponse)
    assert result.body == b"not valid json"
    assert result.media_type == "application/json"
    assert result.status_code == 200


def test_build_json_immediate_response_non_string_non_dict_content() -> None:
    """Non-string, non-dict content falls back to JSONResponse."""

    result = triggers._build_json_immediate_response(12345, 201)

    assert isinstance(result, JSONResponse)
    assert json.loads(result.body) == 12345
    assert result.status_code == 201


@pytest.mark.asyncio()
async def test_queue_webhook_run_translates_quota_error() -> None:
    """Webhook queueing should surface quota errors as HTTP exceptions."""

    workflow_id = uuid4()

    class Repository:
        async def handle_webhook_trigger(self, *args, **kwargs):
            del args, kwargs
            raise WorkspaceQuotaExceededError(
                "Workspace reached its concurrent run limit",
                code="workspace.quota.concurrent_runs",
                details={"limit": 1, "current": 1},
            )

    with pytest.raises(HTTPException) as exc_info:
        await triggers._queue_webhook_run(
            Repository(),
            workflow_id,
            method="POST",
            headers={},
            query_params={},
            payload={},
            source_ip=None,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "workspace.quota.concurrent_runs"


@pytest.mark.asyncio()
async def test_queue_webhook_run_translates_validation_error() -> None:
    """Webhook queueing should translate validation errors into HTTP errors."""

    workflow_id = uuid4()

    class Repository:
        async def handle_webhook_trigger(self, *args, **kwargs):
            del args, kwargs
            raise WebhookValidationError("invalid", status_code=400)

    with pytest.raises(HTTPException) as exc_info:
        await triggers._queue_webhook_run(
            Repository(),
            workflow_id,
            method="POST",
            headers={},
            query_params={},
            payload={},
            source_ip=None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid"


@pytest.mark.asyncio()
async def test_queue_webhook_run_translates_credential_health_error() -> None:
    """Webhook queueing should translate credential health failures."""

    workflow_id = uuid4()
    report = CredentialHealthReport(
        workflow_id=workflow_id,
        results=[
            CredentialHealthResult(
                credential_id=uuid4(),
                name="Slack",
                provider="slack",
                status=CredentialHealthStatus.UNHEALTHY,
                last_checked_at=None,
                failure_reason="expired",
            )
        ],
        checked_at=None,
    )

    class Repository:
        async def handle_webhook_trigger(self, *args, **kwargs):
            del args, kwargs
            raise CredentialHealthError(report)

    with pytest.raises(HTTPException) as exc_info:
        await triggers._queue_webhook_run(
            Repository(),
            workflow_id,
            method="POST",
            headers={},
            query_params={},
            payload={},
            source_ip=None,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["failures"] == ["expired"]
