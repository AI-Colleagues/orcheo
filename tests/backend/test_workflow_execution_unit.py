"""Unit tests for workflow execution helpers."""

from __future__ import annotations
import asyncio
import contextlib
import importlib
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import pytest
from fastapi import WebSocketDisconnect
from orcheo.agentensor.evaluation import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationRequest,
)
from orcheo.agentensor.training import TrainingRequest
from orcheo.graph.ingestion import LANGGRAPH_SCRIPT_FORMAT
from orcheo.runtime.runnable_config import RunnableConfigModel
from orcheo_backend.app import _workflow_execution_module as workflow_execution
from orcheo_backend.app import dependencies as backend_dependencies
from orcheo_backend.app.history import RunHistoryError
from orcheo_backend.app.repository import (
    RepositoryError,
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.workflow_execution import (
    _persist_failure_history,
    _report_history_error,
    _run_evaluation_node,
    _run_training_node,
    execute_workflow,
    execute_workflow_evaluation,
    execute_workflow_training,
)


backend_app_module = importlib.import_module("orcheo_backend.app")


async def _echo_workspace_id(
    repository: Any, workflow_id: UUID | None, workspace_id: str | None = None
) -> str | None:
    del repository, workflow_id
    return workspace_id


def test_report_history_error_logs_and_updates_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_report_history_error should record tracing metadata and log failures."""

    span = SimpleNamespace()
    exc = RuntimeError("boom")
    calls: list[tuple[Any, Any]] = []
    monkeypatch.setattr(
        workflow_execution,
        "record_workflow_failure",
        lambda span_arg, exc_arg: calls.append((span_arg, exc_arg)),
    )

    class StubLogger:
        def __init__(self) -> None:
            self.messages: list[tuple[str, tuple[Any, ...]]] = []

        def exception(self, message: str, *args: Any) -> None:
            self.messages.append((message, args))

    logger_stub = StubLogger()
    monkeypatch.setattr(workflow_execution, "logger", logger_stub)

    _report_history_error("exec-1", span, exc, context="persist history")

    assert calls == [(span, exc)]
    assert logger_stub.messages == [
        ("Failed to %s for execution %s", ("persist history", "exec-1"))
    ]


@pytest.mark.asyncio
async def test_persist_failure_history_reports_store_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_persist_failure_history tolerates RunHistoryError failures."""

    history_store = AsyncMock()
    failure = RunHistoryError("unavailable")
    history_store.append_step.side_effect = failure
    span = SimpleNamespace()
    reports: list[tuple[str, Any, Exception, str]] = []
    monkeypatch.setattr(
        workflow_execution,
        "_report_history_error",
        lambda execution_id, span_arg, exc, *, context: reports.append(
            (execution_id, span_arg, exc, context)
        ),
    )

    await _persist_failure_history(
        history_store,
        "exec-2",
        {"status": "error"},
        "something failed",
        span,
    )

    assert reports == [
        ("exec-2", span, failure, "record failure state"),
    ]


def test_build_initial_state_langgraph_formats() -> None:
    inputs = {"foo": "bar"}
    runtime_config = {"configurable": {"thread_id": "thread"}}
    state = workflow_execution._build_initial_state(
        {"format": LANGGRAPH_SCRIPT_FORMAT}, inputs, None, "workspace-1"
    )
    assert state["inputs"] == inputs
    assert state["results"] == {}
    assert state["messages"] == []
    assert state["workspace_id"] == "workspace-1"
    assert state["config"] == {}

    state_with_config = workflow_execution._build_initial_state(
        {"format": LANGGRAPH_SCRIPT_FORMAT}, inputs, runtime_config, "workspace-1"
    )
    assert state_with_config["config"] == runtime_config
    assert state_with_config["foo"] == "bar"


def test_build_initial_state_langgraph_non_mapping_returns_inputs() -> None:
    data = ["value"]
    runtime_config = {"config": "value"}
    state = workflow_execution._build_initial_state(
        {"format": LANGGRAPH_SCRIPT_FORMAT}, data, runtime_config, "workspace-1"
    )
    assert state is data


def test_build_initial_state_default_structure() -> None:
    state = workflow_execution._build_initial_state(
        {"format": "graph"}, {"foo": "bar"}, {"config": "value"}, "workspace-1"
    )
    assert state["messages"] == []
    assert state["inputs"]["foo"] == "bar"
    assert state["workspace_id"] == "workspace-1"


def test_prepare_runnable_config_accepts_model() -> None:
    model = RunnableConfigModel(tags=["test"])
    parsed, runtime_config, state_config, stored_config = (
        workflow_execution._prepare_runnable_config("exec-1", model)
    )

    assert parsed.tags == ["test"]
    assert runtime_config["configurable"]["thread_id"] == "exec-1"
    assert state_config["tags"] == ["test"]
    assert stored_config["tags"] == ["test"]


def test_prepare_runnable_config_uses_stored_defaults() -> None:
    stored = {"tags": ["stored"], "metadata": {"team": "ops"}}
    parsed, _, state_config, _ = workflow_execution._prepare_runnable_config(
        "exec-1",
        None,
        stored,
    )
    assert parsed.tags == ["stored"]
    assert state_config["metadata"] == {"team": "ops"}


def test_prepare_runnable_config_merges_overrides() -> None:
    stored = {"metadata": {"team": "ops", "env": "prod"}, "recursion_limit": 5}
    override = {"metadata": {"env": "stage"}, "tags": ["run"], "max_concurrency": 2}
    parsed, _, state_config, _ = workflow_execution._prepare_runnable_config(
        "exec-1",
        override,
        stored,
    )
    assert parsed.tags == ["run"]
    assert parsed.recursion_limit == 5
    assert parsed.max_concurrency == 2
    assert state_config["metadata"] == {"team": "ops", "env": "stage"}


@pytest.mark.asyncio
async def test_resolve_stored_runnable_config_handles_repository_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve runnable config falls back to empty config on repository errors."""

    class Repository:
        async def get_latest_version(self, workflow_id: UUID) -> Any:
            raise RepositoryError("db unavailable")

    monkeypatch.setattr(backend_dependencies, "get_repository", lambda: Repository())

    result = await workflow_execution._resolve_stored_runnable_config(
        uuid4(),
        None,
    )

    assert result == {}


@pytest.mark.asyncio
async def test_resolve_stored_runnable_config_returns_none_for_missing_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Repository:
        async def get_latest_version(self, workflow_id: UUID) -> Any:
            raise WorkflowNotFoundError("missing")

    monkeypatch.setattr(backend_dependencies, "get_repository", lambda: Repository())

    result = await workflow_execution._resolve_stored_runnable_config(
        uuid4(),
        None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_stored_runnable_config_returns_none_for_missing_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Repository:
        async def get_latest_version(self, workflow_id: UUID) -> Any:
            raise WorkflowVersionNotFoundError("missing version")

    monkeypatch.setattr(backend_dependencies, "get_repository", lambda: Repository())

    result = await workflow_execution._resolve_stored_runnable_config(
        uuid4(),
        None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_stored_runnable_config_returns_version_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Version:
        runnable_config = {"worker": "value"}

    class Repository:
        async def get_latest_version(self, workflow_id: UUID) -> Any:
            return Version()

    monkeypatch.setattr(backend_dependencies, "get_repository", lambda: Repository())

    result = await workflow_execution._resolve_stored_runnable_config(
        uuid4(),
        None,
    )

    assert result == {"worker": "value"}


@pytest.mark.asyncio
async def test_resolve_stored_runnable_config_uses_cached_value() -> None:
    cached = {"always": "there"}
    result = await workflow_execution._resolve_stored_runnable_config(
        uuid4(),
        cached,
    )

    assert result == cached


@pytest.mark.asyncio
async def test_execute_workflow_reports_history_store_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_workflow should surface RunHistoryError exceptions with telemetry."""

    history_store = AsyncMock()
    history_store.start_run = AsyncMock()
    history_store.append_step = AsyncMock()
    history_store.mark_cancelled = AsyncMock()
    history_store.mark_failed = AsyncMock()
    history_store.mark_completed = AsyncMock()
    monkeypatch.setattr(workflow_execution, "get_history_store", lambda: history_store)
    monkeypatch.setattr(workflow_execution, "get_settings", lambda: {"dummy": True})

    class DummyVault:
        def list_all_credentials(self) -> list[Any]:
            return []

    monkeypatch.setattr(workflow_execution, "get_vault", lambda: DummyVault())
    monkeypatch.setattr(workflow_execution, "get_repository", lambda: object())
    monkeypatch.setattr(
        workflow_execution, "resolve_workflow_workspace_id", _echo_workspace_id
    )
    monkeypatch.setattr(
        workflow_execution,
        "credential_context_from_workflow",
        lambda workflow_id, workspace_id=None: {"workflow_id": workflow_id},
    )
    monkeypatch.setattr(
        workflow_execution, "CredentialResolver", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        workflow_execution, "credential_resolution", contextlib.nullcontext
    )

    class DummyCheckpointer:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(
        backend_app_module,
        "create_checkpointer",
        lambda settings: DummyCheckpointer(),
    )

    class DummyGraph:
        def compile(
            self,
            *,
            checkpointer: object,
            store: object | None = None,
        ) -> object:
            del checkpointer, store
            return object()

    monkeypatch.setattr(backend_app_module, "build_graph", lambda config: DummyGraph())
    monkeypatch.setattr(
        backend_app_module,
        "create_graph_store",
        lambda settings: DummyCheckpointer(),
    )
    monkeypatch.setattr(workflow_execution, "get_tracer", lambda name: object())

    class StubSpanContext:
        def __init__(self) -> None:
            self.span = object()
            self.trace_id = "trace-id"
            self.started_at = datetime.now(tz=UTC)

    @contextlib.contextmanager
    def fake_workflow_span(*args: Any, **kwargs: Any):
        yield StubSpanContext()

    monkeypatch.setattr(workflow_execution, "workflow_span", fake_workflow_span)

    run_error = RunHistoryError("history store unavailable")
    stream_mock = AsyncMock(side_effect=run_error)
    monkeypatch.setattr(workflow_execution, "_stream_workflow_updates", stream_mock)
    reports: list[tuple[str, Any, Exception, str]] = []
    monkeypatch.setattr(
        workflow_execution,
        "_report_history_error",
        lambda execution_id, span, exc, *, context: reports.append(
            (execution_id, span, exc, context)
        ),
    )

    class DummyWebSocket:
        def __init__(self) -> None:
            self.messages: list[Any] = []

        async def send_json(self, payload: Any) -> None:
            self.messages.append(payload)

    websocket = DummyWebSocket()

    with pytest.raises(RunHistoryError):
        await execute_workflow(
            workflow_id="00000000-0000-0000-0000-000000000000",
            graph_config={"format": "graph"},
            inputs={"foo": "bar"},
            execution_id="exec-3",
            websocket=websocket,  # type: ignore[arg-type]
        )

    assert len(reports) == 1
    execution_id, span_arg, exc_arg, context = reports[0]
    assert execution_id == "exec-3"
    assert exc_arg is run_error
    assert context == "persist workflow history"


@pytest.mark.asyncio
async def test_emit_trace_update_ignores_history_errors() -> None:
    """_emit_trace_update should swallow RunHistoryError exceptions."""

    history_store = AsyncMock()
    history_store.get_history = AsyncMock(side_effect=RunHistoryError("missing"))

    class DummyWebSocket:
        def __init__(self) -> None:
            self.send_json = AsyncMock()

    websocket = DummyWebSocket()

    await workflow_execution._emit_trace_update(
        history_store,
        websocket,
        execution_id="exec-missing",
    )

    history_store.get_history.assert_awaited_once_with("exec-missing")
    assert websocket.send_json.await_count == 0


@pytest.mark.asyncio
async def test_safe_send_json_handles_disconnect() -> None:
    """Websocket disconnections during _safe_send_json return False."""

    websocket = AsyncMock()
    websocket.send_json.side_effect = WebSocketDisconnect()

    assert (
        await workflow_execution._safe_send_json(websocket, {"status": "payload"})
    ) is False
    websocket.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_send_json_handles_closed_state() -> None:
    """RuntimeErrors after a close are skipped."""

    websocket = AsyncMock()
    websocket.send_json.side_effect = RuntimeError(
        workflow_execution._CANNOT_SEND_AFTER_CLOSE
    )

    assert (
        await workflow_execution._safe_send_json(websocket, {"status": "payload"})
    ) is False
    websocket.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_send_json_propagates_other_errors() -> None:
    """RuntimeErrors unrelated to close should bubble up."""

    websocket = AsyncMock()
    error = RuntimeError("unexpected")
    websocket.send_json.side_effect = error

    with pytest.raises(RuntimeError):
        await workflow_execution._safe_send_json(websocket, {})


def test_sanitize_public_step_payload_strips_trace_metadata() -> None:
    """Workflow websocket payloads should omit trace-only metadata."""

    payload = {
        "draft": {
            "messages": [{"role": "assistant", "content": "done"}],
            "__trace": {
                "ai": {
                    "kind": "llm",
                    "requested_model": "openai:gpt-4o-mini",
                }
            },
        }
    }

    sanitized = workflow_execution._sanitize_public_step_payload(payload)

    assert sanitized == {
        "draft": {
            "messages": [{"role": "assistant", "content": "done"}],
        }
    }


def test_patched_environment_restores_existing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "ORCHEO_WORKFLOW_EXECUTION_TEST_ENV"
    monkeypatch.setenv(key, "original")

    with workflow_execution._patched_environment({key: "override"}):
        assert os.environ[key] == "override"

    assert os.environ[key] == "original"


def test_sensitive_debug_helpers_log_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[tuple[str, tuple[object, ...]]] = []

    class LoggerStub:
        def debug(self, message: str, *args: object) -> None:
            messages.append((message, args))

    monkeypatch.setattr(backend_app_module, "logger", LoggerStub())
    workflow_execution.configure_sensitive_logging(enable_sensitive_debug=True)
    try:
        workflow_execution._log_sensitive_debug("inputs: %s", {"foo": "bar"})
        workflow_execution._log_step_debug({"node": {"status": "ok"}})
        workflow_execution._log_final_state_debug({"reply": "done"})
    finally:
        workflow_execution.configure_sensitive_logging(enable_sensitive_debug=False)

    assert messages


def test_log_final_state_debug_noops_when_disabled() -> None:
    workflow_execution.configure_sensitive_logging(enable_sensitive_debug=False)
    workflow_execution._log_final_state_debug({"reply": "done"})


@pytest.mark.asyncio
async def test_safe_send_json_returns_true_on_success() -> None:
    websocket = AsyncMock()

    assert await workflow_execution._safe_send_json(websocket, {"status": "ok"}) is True
    websocket.send_json.assert_awaited_once_with({"status": "ok"})


@pytest.mark.asyncio
async def test_emit_trace_update_sends_payload_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.app.history import RunHistoryRecord

    history_store = AsyncMock()
    history_store.get_history = AsyncMock(
        return_value=RunHistoryRecord(workflow_id="wf", execution_id="exec")
    )
    websocket = AsyncMock()

    class Update:
        def model_dump(self, mode: str = "json") -> dict[str, object]:
            return {"trace": "payload"}

    monkeypatch.setattr(
        workflow_execution, "build_trace_update", lambda *args, **kwargs: Update()
    )

    await workflow_execution._emit_trace_update(history_store, websocket, "exec")

    websocket.send_json.assert_awaited_once_with({"trace": "payload"})


@pytest.mark.asyncio
async def test_emit_trace_update_skips_when_builder_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.app.history import RunHistoryRecord

    history_store = AsyncMock()
    history_store.get_history = AsyncMock(
        return_value=RunHistoryRecord(workflow_id="wf", execution_id="exec")
    )
    websocket = AsyncMock()
    monkeypatch.setattr(
        workflow_execution, "build_trace_update", lambda *args, **kwargs: None
    )

    await workflow_execution._emit_trace_update(history_store, websocket, "exec")

    websocket.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_workflow_updates_logs_final_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final_state_logs: list[object] = []
    safe_send = AsyncMock(return_value=True)
    emit_update = AsyncMock()
    history_store = AsyncMock()
    history_store.append_step = AsyncMock(return_value=SimpleNamespace())

    class CompiledGraph:
        async def astream(self, state: object, *, config: object, stream_mode: str):
            yield {"node": {"status": "running"}}

        async def aget_state(self, config: object) -> object:
            return SimpleNamespace(values={"reply": "done"})

    monkeypatch.setattr(workflow_execution, "_safe_send_json", safe_send)
    monkeypatch.setattr(workflow_execution, "_emit_trace_update", emit_update)
    monkeypatch.setattr(
        workflow_execution, "record_workflow_step", lambda tracer, payload: None
    )
    monkeypatch.setattr(
        workflow_execution,
        "_log_final_state_debug",
        lambda values: final_state_logs.append(values),
    )

    await workflow_execution._stream_workflow_updates(
        CompiledGraph(),
        {"inputs": {}},
        {"configurable": {"thread_id": "exec"}},
        history_store,
        "exec",
        AsyncMock(),
        object(),
    )

    assert final_state_logs == [{"reply": "done"}]


@pytest.mark.asyncio
async def test_stream_workflow_updates_forwards_in_node_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-node status emissions should be persisted and streamed to clients."""

    safe_send = AsyncMock(return_value=True)
    emit_update = AsyncMock()
    history_store = AsyncMock()
    history_step = SimpleNamespace()
    history_store.append_step = AsyncMock(return_value=history_step)
    captured_callback: dict[str, Any] = {}

    from orcheo.nodes.agent_tools.context import get_active_tool_progress_callback

    class CompiledGraph:
        async def astream(self, state: object, *, config: object, stream_mode: str):
            captured_callback["callback"] = get_active_tool_progress_callback()
            assert captured_callback["callback"] is not None
            await captured_callback["callback"](
                {
                    "node": "indexer",
                    "event": "node_status",
                    "payload": {"status": "loading"},
                }
            )
            # Sub-graph progress without an event key must not be forwarded.
            await captured_callback["callback"]({"sub_node": {"value": 1}})
            yield {"indexer": {"value": "done"}}

        async def aget_state(self, config: object) -> object:
            return SimpleNamespace(values={"reply": "done"})

    monkeypatch.setattr(workflow_execution, "_safe_send_json", safe_send)
    monkeypatch.setattr(workflow_execution, "_emit_trace_update", emit_update)
    monkeypatch.setattr(
        workflow_execution, "record_workflow_step", lambda tracer, payload: None
    )
    monkeypatch.setattr(
        workflow_execution, "_log_final_state_debug", lambda values: None
    )

    await workflow_execution._stream_workflow_updates(
        CompiledGraph(),
        {"inputs": {}},
        {"configurable": {"thread_id": "exec"}},
        history_store,
        "exec",
        AsyncMock(),
        object(),
    )

    appended = [call.args[1] for call in history_store.append_step.await_args_list]
    assert {
        "node": "indexer",
        "event": "node_status",
        "payload": {"status": "loading"},
    } in appended
    assert {"indexer": {"value": "done"}} in appended
    # Sub-graph payload without an event key must be filtered out.
    assert {"sub_node": {"value": 1}} not in appended
    # Callback must be unbound after the loop completes.
    assert get_active_tool_progress_callback() is None


@pytest.mark.asyncio
async def test_run_workflow_stream_handles_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history_store = AsyncMock()
    emit_update = AsyncMock()
    cancellation_calls: list[str | None] = []
    monkeypatch.setattr(
        workflow_execution,
        "_stream_workflow_updates",
        AsyncMock(side_effect=asyncio.CancelledError("stop")),
    )
    monkeypatch.setattr(
        workflow_execution,
        "_emit_trace_update",
        emit_update,
    )
    monkeypatch.setattr(
        workflow_execution,
        "record_workflow_cancellation",
        lambda span, reason=None: cancellation_calls.append(reason),
    )

    with pytest.raises(asyncio.CancelledError):
        await workflow_execution._run_workflow_stream(
            object(),
            {},
            {},
            history_store,
            "exec-cancel",
            AsyncMock(),
            object(),
            object(),
        )

    history_store.append_step.assert_awaited_once_with(
        "exec-cancel",
        {"status": "cancelled", "reason": "stop"},
    )
    history_store.mark_cancelled.assert_awaited_once_with("exec-cancel", reason="stop")
    assert cancellation_calls == ["stop"]
    emit_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_workflow_stream_handles_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history_store = AsyncMock()
    emit_update = AsyncMock()
    persist_history = AsyncMock()
    failure_calls: list[Exception] = []
    error = RuntimeError("boom")
    span = SimpleNamespace()
    monkeypatch.setattr(
        workflow_execution,
        "_stream_workflow_updates",
        AsyncMock(side_effect=error),
    )
    monkeypatch.setattr(workflow_execution, "_emit_trace_update", emit_update)
    monkeypatch.setattr(
        workflow_execution,
        "_persist_failure_history",
        persist_history,
    )
    monkeypatch.setattr(
        workflow_execution,
        "record_workflow_failure",
        lambda span, exc: failure_calls.append(exc),
    )

    with pytest.raises(RuntimeError):
        await workflow_execution._run_workflow_stream(
            object(),
            {},
            {},
            history_store,
            "exec-failed",
            AsyncMock(),
            object(),
            span,
        )

    persist_history.assert_awaited_once_with(
        history_store,
        "exec-failed",
        {"status": "error", "error": "boom"},
        "boom",
        span,
    )
    assert failure_calls == [error]
    emit_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_workflow_stream_reports_history_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history_store = AsyncMock()
    emit_update = AsyncMock()
    reports: list[tuple[str, Any, Exception, str]] = []
    span = SimpleNamespace()
    tracer = SimpleNamespace()
    run_error = RunHistoryError("history unavailable")

    monkeypatch.setattr(
        workflow_execution,
        "_stream_workflow_updates",
        AsyncMock(side_effect=run_error),
    )
    monkeypatch.setattr(
        workflow_execution,
        "_report_history_error",
        lambda execution_id, span_arg, exc_arg, *, context: reports.append(
            (execution_id, span_arg, exc_arg, context)
        ),
    )

    with pytest.raises(RunHistoryError):
        await workflow_execution._run_workflow_stream(
            compiled_graph=object(),
            state={},
            config={},
            history_store=history_store,
            execution_id="exec-history",
            websocket=emit_update,
            tracer=tracer,
            span=span,
        )

    assert reports == [("exec-history", span, run_error, "persist workflow history")]


@pytest.mark.asyncio
async def test_persist_failure_history_marks_run_failed() -> None:
    history_store = AsyncMock()

    await workflow_execution._persist_failure_history(
        history_store,
        "exec-ok",
        {"status": "error"},
        "boom",
        SimpleNamespace(),
    )

    history_store.append_step.assert_awaited_once_with("exec-ok", {"status": "error"})
    history_store.mark_failed.assert_awaited_once_with("exec-ok", "boom")


@pytest.mark.asyncio
async def test_execute_workflow_completes_for_invalid_workflow_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history_store = AsyncMock()
    history_store.start_run = AsyncMock()
    history_store.append_step = AsyncMock(return_value=SimpleNamespace())
    history_store.mark_completed = AsyncMock()
    websocket = AsyncMock()
    tracer = object()
    safe_send = AsyncMock(return_value=True)
    emit_update = AsyncMock()

    monkeypatch.setattr(workflow_execution, "get_settings", lambda: {})
    monkeypatch.setattr(workflow_execution, "get_history_store", lambda: history_store)
    monkeypatch.setattr(workflow_execution, "get_repository", lambda: object())
    monkeypatch.setattr(
        workflow_execution, "resolve_workflow_workspace_id", _echo_workspace_id
    )

    class DummyVault:
        def list_all_credentials(self) -> list[Any]:
            return []

    monkeypatch.setattr(workflow_execution, "get_vault", lambda: DummyVault())
    monkeypatch.setattr(
        workflow_execution,
        "credential_context_from_workflow",
        lambda workflow_id, workspace_id=None: {"workflow_id": workflow_id},
    )
    monkeypatch.setattr(
        workflow_execution,
        "CredentialResolver",
        lambda vault, context=None: object(),
    )
    monkeypatch.setattr(
        workflow_execution, "credential_resolution", contextlib.nullcontext
    )
    monkeypatch.setattr(workflow_execution, "get_tracer", lambda name: tracer)
    monkeypatch.setattr(workflow_execution, "_safe_send_json", safe_send)
    monkeypatch.setattr(workflow_execution, "_emit_trace_update", emit_update)
    monkeypatch.setattr(
        workflow_execution,
        "_run_workflow_stream",
        AsyncMock(),
    )
    monkeypatch.setattr(
        workflow_execution,
        "_external_agent_provider_environment",
        lambda: {},
    )
    monkeypatch.setattr(
        workflow_execution,
        "_resolve_stored_runnable_config",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        workflow_execution,
        "record_workflow_completion",
        lambda span: None,
    )

    class DummyMergedConfig:
        tags: list[object] = []
        callbacks: list[object] = []
        metadata: dict[str, object] = {}
        run_name = None

        def to_runnable_config(self, execution_id: str) -> dict[str, object]:
            return {"configurable": {"thread_id": execution_id}}

        def to_state_config(self, execution_id: str) -> dict[str, object]:
            return {"configurable": {"thread_id": execution_id}}

        def to_json_config(self, execution_id: str) -> dict[str, object]:
            return {"configurable": {"thread_id": execution_id}}

    monkeypatch.setattr(
        workflow_execution,
        "merge_runnable_configs",
        lambda stored, candidate: DummyMergedConfig(),
    )

    class DummyCompiledGraph:
        pass

    class DummyGraph:
        def compile(
            self, *, checkpointer: object, store: object | None = None
        ) -> object:
            return DummyCompiledGraph()

    class DummyAsyncContext:
        def __init__(self, value: object) -> None:
            self.value = value

        async def __aenter__(self) -> object:
            return self.value

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(backend_app_module, "build_graph", lambda config: DummyGraph())
    monkeypatch.setattr(
        backend_app_module,
        "create_checkpointer",
        lambda settings: DummyAsyncContext("checkpointer"),
    )
    monkeypatch.setattr(
        backend_app_module,
        "create_graph_store",
        lambda settings: DummyAsyncContext("graph_store"),
    )

    span_context = SimpleNamespace(
        span=object(),
        trace_id="trace-id",
        started_at=datetime.now(tz=UTC),
    )

    @contextlib.contextmanager
    def fake_workflow_span(*args: Any, **kwargs: Any):
        yield span_context

    monkeypatch.setattr(workflow_execution, "workflow_span", fake_workflow_span)

    await execute_workflow(
        workflow_id="not-a-uuid",
        graph_config={"nodes": []},
        inputs={"foo": "bar"},
        execution_id="exec-success",
        websocket=websocket,
    )

    history_store.start_run.assert_awaited_once()
    history_store.append_step.assert_any_await("exec-success", {"status": "completed"})
    history_store.mark_completed.assert_awaited_once_with("exec-success")
    safe_send.assert_any_await(websocket, {"status": "completed"})
    assert emit_update.await_args_list[-1].kwargs["complete"] is True


@pytest.mark.asyncio
async def test_execute_node_runs_node_with_prepared_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workflow_execution, "get_vault", lambda: object())
    monkeypatch.setattr(workflow_execution, "get_repository", lambda: object())
    monkeypatch.setattr(
        workflow_execution, "resolve_workflow_workspace_id", _echo_workspace_id
    )
    monkeypatch.setattr(
        workflow_execution,
        "credential_context_from_workflow",
        lambda workflow_id, workspace_id=None: {"workflow_id": workflow_id},
    )
    monkeypatch.setattr(
        workflow_execution,
        "CredentialResolver",
        lambda vault, context=None: object(),
    )
    monkeypatch.setattr(
        workflow_execution, "credential_resolution", contextlib.nullcontext
    )
    monkeypatch.setattr(
        workflow_execution,
        "_external_agent_provider_environment",
        lambda: {"EXTERNAL_AGENT": "1"},
    )
    monkeypatch.setattr(workflow_execution.uuid, "uuid4", lambda: UUID(int=99))

    class DummyMergedConfig:
        def to_runnable_config(self, execution_id: str) -> dict[str, object]:
            return {"configurable": {"thread_id": execution_id}}

        def to_state_config(self, execution_id: str) -> dict[str, object]:
            return {"configurable": {"thread_id": execution_id}}

        def to_json_config(self, execution_id: str) -> dict[str, object]:
            return {}

    monkeypatch.setattr(
        workflow_execution,
        "merge_runnable_configs",
        lambda stored, candidate: DummyMergedConfig(),
    )

    class NodeStub:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def __call__(
            self, state: Any, runtime_config: object
        ) -> dict[str, object]:
            return {
                "state": state,
                "runtime_config": runtime_config,
            }

    result = await workflow_execution.execute_node(
        NodeStub,
        {"name": "node"},
        {"message": "hello"},
        workflow_id=UUID(int=7),
    )

    assert result["runtime_config"] == {
        "configurable": {"thread_id": str(UUID(int=99))}
    }
    assert result["state"]["inputs"] == {"message": "hello"}


def _patch_graph_and_checkpointer(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyGraph:
        def compile(
            self,
            *,
            checkpointer: object,
            store: object | None = None,
        ) -> object:
            del checkpointer, store
            return object()

    class DummyCheckpointer:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(backend_app_module, "build_graph", lambda config: DummyGraph())
    monkeypatch.setattr(
        backend_app_module,
        "create_checkpointer",
        lambda settings: DummyCheckpointer(),
    )
    monkeypatch.setattr(
        backend_app_module,
        "create_graph_store",
        lambda settings: DummyCheckpointer(),
    )


def _patch_agentensor_node(
    monkeypatch: pytest.MonkeyPatch,
    node_name: str,
    *,
    result: dict[str, Any] | None = None,
    exc: Exception | None = None,
) -> None:
    class NodeStub:
        def __init__(
            self, *, name: str, mode: str, progress_callback=None, **kwargs: Any
        ) -> None:
            self.name = node_name
            self.mode = mode
            self.progress_callback = progress_callback

        async def __call__(self, state: Any, runtime_config: object) -> dict[str, Any]:
            if exc is not None:
                raise exc
            return result or {"results": {self.name: {"value": "ok"}}}

    monkeypatch.setattr(workflow_execution, "AgentensorNode", NodeStub)


def _setup_common_mocks(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    safe_send = AsyncMock()
    emit_update = AsyncMock()
    monkeypatch.setattr(workflow_execution, "_safe_send_json", safe_send)
    monkeypatch.setattr(workflow_execution, "_emit_trace_update", emit_update)
    monkeypatch.setattr(
        workflow_execution, "credential_resolution", contextlib.nullcontext
    )
    return safe_send, emit_update


def _make_history_store() -> AsyncMock:
    history_store = AsyncMock()
    history_store.append_step = AsyncMock()
    history_store.mark_cancelled = AsyncMock()
    history_store.mark_failed = AsyncMock()
    history_store.mark_completed = AsyncMock()
    return history_store


def _evaluation_request() -> EvaluationRequest:
    dataset = EvaluationDataset(
        id="dataset",
        cases=[EvaluationCase(inputs={"foo": "bar"})],
    )
    return EvaluationRequest(dataset=dataset)


@pytest.mark.asyncio
async def test_run_evaluation_node_sends_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send, emit_update = _setup_common_mocks(monkeypatch)
    _patch_graph_and_checkpointer(monkeypatch)
    result_payload = {"results": {"agentensor_evaluator": {"score": 1}}}
    _patch_agentensor_node(
        monkeypatch,
        "agentensor_evaluator",
        result=result_payload,
    )
    history_store = _make_history_store()
    final_step = SimpleNamespace()
    history_store.append_step = AsyncMock(return_value=final_step)

    parsed_config = SimpleNamespace(
        prompts={"foo": "bar"},
        tags=[],
        callbacks=[],
        metadata={},
        run_name=None,
    )
    websocket = object()

    await _run_evaluation_node(
        graph_config={},
        inputs={},
        runtime_config=SimpleNamespace(),
        state_config={},
        evaluation_request=_evaluation_request(),
        parsed_config=parsed_config,
        history_store=history_store,
        websocket=websocket,
        execution_id="eval-1",
        tracer=object(),
        resolver=object(),
        settings={},
        span=SimpleNamespace(),
    )

    expected_payload = {
        "node": "agentensor_evaluator",
        "event": "evaluation_result",
        "payload": {"score": 1},
    }
    history_store.append_step.assert_awaited_once_with("eval-1", expected_payload)
    safe_send.assert_awaited_once_with(websocket, expected_payload)
    emit_update.assert_awaited_once_with(
        history_store,
        websocket,
        "eval-1",
        step=final_step,
    )


@pytest.mark.asyncio
async def test_run_evaluation_node_handles_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send, emit_update = _setup_common_mocks(monkeypatch)
    _patch_graph_and_checkpointer(monkeypatch)
    cancel_reason = asyncio.CancelledError("timeout")
    _patch_agentensor_node(
        monkeypatch,
        "agentensor_evaluator",
        exc=cancel_reason,
    )
    history_store = _make_history_store()
    history_store.append_step = AsyncMock(return_value=SimpleNamespace())
    cancel_calls: list[str | None] = []
    monkeypatch.setattr(
        workflow_execution,
        "record_workflow_cancellation",
        lambda span, reason=None: cancel_calls.append(reason),
    )
    websocket = object()

    with pytest.raises(asyncio.CancelledError):
        await _run_evaluation_node(
            graph_config={},
            inputs={},
            runtime_config=SimpleNamespace(),
            state_config={},
            evaluation_request=_evaluation_request(),
            parsed_config=SimpleNamespace(
                prompts={"foo": "bar"},
                tags=[],
                callbacks=[],
                metadata={},
                run_name=None,
            ),
            history_store=history_store,
            websocket=websocket,
            execution_id="eval-2",
            tracer=object(),
            resolver=object(),
            settings={},
            span=SimpleNamespace(),
        )

    cancellation_payload = {"status": "cancelled", "reason": "timeout"}
    history_store.append_step.assert_awaited_once_with("eval-2", cancellation_payload)
    history_store.mark_cancelled.assert_awaited_once_with("eval-2", reason="timeout")
    assert cancel_calls == ["timeout"]
    emit_update.assert_awaited_once_with(
        history_store,
        websocket,
        "eval-2",
        include_root=True,
        complete=True,
    )
    safe_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evaluation_node_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send, emit_update = _setup_common_mocks(monkeypatch)
    _patch_graph_and_checkpointer(monkeypatch)
    error = RuntimeError("boom")
    _patch_agentensor_node(
        monkeypatch,
        "agentensor_evaluator",
        exc=error,
    )
    history_store = _make_history_store()
    persist_history = AsyncMock()
    monkeypatch.setattr(workflow_execution, "_persist_failure_history", persist_history)
    failure_calls: list[Exception] = []
    span = SimpleNamespace()
    monkeypatch.setattr(
        workflow_execution,
        "record_workflow_failure",
        lambda span_arg, exc_arg: failure_calls.append(exc_arg),
    )
    websocket = object()

    with pytest.raises(RuntimeError):
        await _run_evaluation_node(
            graph_config={},
            inputs={},
            runtime_config=SimpleNamespace(),
            state_config={},
            evaluation_request=_evaluation_request(),
            parsed_config=SimpleNamespace(
                prompts={"foo": "bar"},
                tags=[],
                callbacks=[],
                metadata={},
                run_name=None,
            ),
            history_store=history_store,
            websocket=websocket,
            execution_id="eval-3",
            tracer=object(),
            resolver=object(),
            settings={},
            span=span,
        )

    persist_history.assert_awaited_once_with(
        history_store,
        "eval-3",
        {"status": "error", "error": "boom"},
        "boom",
        span,
    )
    emit_update.assert_awaited_once_with(
        history_store,
        websocket,
        "eval-3",
        include_root=True,
        complete=True,
    )
    assert failure_calls == [error]
    safe_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evaluation_node_reports_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send, emit_update = _setup_common_mocks(monkeypatch)
    _patch_graph_and_checkpointer(monkeypatch)

    progress_payload = {"node": "agentensor_evaluator", "event": "evaluation_progress"}
    step_progress = SimpleNamespace()
    step_final = SimpleNamespace()

    class ProgressNode:
        def __init__(
            self, *, name: str, mode: str, progress_callback=None, **kwargs: Any
        ) -> None:
            self.name = name
            self.mode = mode
            self.progress_callback = progress_callback

        async def __call__(self, state: Any, runtime_config: object) -> dict[str, Any]:
            if self.progress_callback is not None:
                await self.progress_callback(progress_payload)
            return {"results": {self.name: {"score": 1}}}

    monkeypatch.setattr(workflow_execution, "AgentensorNode", ProgressNode)
    history_store = _make_history_store()
    history_store.append_step = AsyncMock(side_effect=[step_progress, step_final])
    monkeypatch.setattr(
        workflow_execution, "record_workflow_step", lambda tracer, payload: None
    )

    await _run_evaluation_node(
        graph_config={},
        inputs={},
        runtime_config=SimpleNamespace(),
        state_config={},
        evaluation_request=_evaluation_request(),
        parsed_config=SimpleNamespace(
            prompts={"foo": "bar"},
            tags=[],
            callbacks=[],
            metadata={},
            run_name=None,
        ),
        history_store=history_store,
        websocket=object(),
        execution_id="eval-progress",
        tracer=object(),
        resolver=object(),
        settings={},
        span=SimpleNamespace(),
    )

    assert history_store.append_step.await_args_list[0][0][1] == progress_payload
    assert any(call.args[1] == progress_payload for call in safe_send.await_args_list)
    assert emit_update.await_args_list[0][1]["step"] is step_progress


@pytest.mark.asyncio
async def test_run_training_node_reports_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send, emit_update = _setup_common_mocks(monkeypatch)
    _patch_graph_and_checkpointer(monkeypatch)

    progress_payload = {"node": "agentensor_trainer", "event": "training_progress"}
    step_progress = SimpleNamespace()
    step_final = SimpleNamespace()

    class ProgressNode:
        def __init__(
            self, *, name: str, mode: str, progress_callback=None, **kwargs: Any
        ) -> None:
            self.name = name
            self.mode = mode
            self.progress_callback = progress_callback

        async def __call__(self, state: Any, runtime_config: object) -> dict[str, Any]:
            if self.progress_callback is not None:
                await self.progress_callback(progress_payload)
            return {"results": {self.name: {"score": 1}}}

    monkeypatch.setattr(workflow_execution, "AgentensorNode", ProgressNode)
    history_store = _make_history_store()
    history_store.append_step = AsyncMock(side_effect=[step_progress, step_final])
    monkeypatch.setattr(
        workflow_execution, "record_workflow_step", lambda tracer, payload: None
    )

    await _run_training_node(
        workflow_id="workflow-progress",
        graph_config={},
        inputs={},
        runtime_config=SimpleNamespace(),
        state_config={},
        training_request=TrainingRequest(
            dataset=EvaluationDataset(
                id="ds", cases=[EvaluationCase(inputs={"foo": "bar"})]
            )
        ),
        parsed_config=SimpleNamespace(
            prompts={"foo": "bar"},
            tags=[],
            callbacks=[],
            metadata={},
            run_name=None,
        ),
        history_store=history_store,
        websocket=object(),
        execution_id="train-progress",
        tracer=object(),
        resolver=object(),
        settings={},
        span=SimpleNamespace(),
        checkpoint_store=object(),
    )

    assert history_store.append_step.await_args_list[0][0][1] == progress_payload
    assert any(call.args[1] == progress_payload for call in safe_send.await_args_list)
    assert emit_update.await_args_list[0][1]["step"] is step_progress


@pytest.mark.asyncio
async def test_run_training_node_sends_result(monkeypatch: pytest.MonkeyPatch) -> None:
    safe_send, emit_update = _setup_common_mocks(monkeypatch)
    _patch_graph_and_checkpointer(monkeypatch)
    result_payload = {"results": {"agentensor_trainer": {"epoch": 1}}}
    _patch_agentensor_node(
        monkeypatch,
        "agentensor_trainer",
        result=result_payload,
    )
    history_store = _make_history_store()
    final_step = SimpleNamespace()
    history_store.append_step = AsyncMock(return_value=final_step)

    parsed_config = SimpleNamespace(
        prompts={"foo": "bar"},
        tags=[],
        callbacks=[],
        metadata={},
        run_name=None,
    )
    websocket = object()

    await _run_training_node(
        workflow_id="workflow-1",
        graph_config={},
        inputs={},
        runtime_config=SimpleNamespace(),
        state_config={},
        training_request=TrainingRequest(
            dataset=EvaluationDataset(
                id="ds", cases=[EvaluationCase(inputs={"foo": "bar"})]
            )
        ),
        parsed_config=parsed_config,
        history_store=history_store,
        websocket=websocket,
        execution_id="train-1",
        tracer=object(),
        resolver=object(),
        settings={},
        span=SimpleNamespace(),
        checkpoint_store=object(),
    )

    expected_payload = {
        "node": "agentensor_trainer",
        "event": "training_result",
        "payload": {"epoch": 1},
    }
    history_store.append_step.assert_awaited_once_with("train-1", expected_payload)
    safe_send.assert_awaited_once_with(websocket, expected_payload)
    emit_update.assert_awaited_once_with(
        history_store,
        websocket,
        "train-1",
        step=final_step,
    )


@pytest.mark.asyncio
async def test_run_training_node_handles_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send, emit_update = _setup_common_mocks(monkeypatch)
    _patch_graph_and_checkpointer(monkeypatch)
    cancel_reason = asyncio.CancelledError("timeout")
    _patch_agentensor_node(
        monkeypatch,
        "agentensor_trainer",
        exc=cancel_reason,
    )
    history_store = _make_history_store()
    history_store.append_step = AsyncMock(return_value=SimpleNamespace())
    cancel_calls: list[str | None] = []
    monkeypatch.setattr(
        workflow_execution,
        "record_workflow_cancellation",
        lambda span, reason=None: cancel_calls.append(reason),
    )
    websocket = object()

    with pytest.raises(asyncio.CancelledError):
        await _run_training_node(
            workflow_id="workflow-2",
            graph_config={},
            inputs={},
            runtime_config=SimpleNamespace(),
            state_config={},
            training_request=TrainingRequest(
                dataset=EvaluationDataset(
                    id="ds", cases=[EvaluationCase(inputs={"foo": "bar"})]
                )
            ),
            parsed_config=SimpleNamespace(
                prompts={"foo": "bar"},
                tags=[],
                callbacks=[],
                metadata={},
                run_name=None,
            ),
            history_store=history_store,
            websocket=websocket,
            execution_id="train-2",
            tracer=object(),
            resolver=object(),
            settings={},
            span=SimpleNamespace(),
            checkpoint_store=object(),
        )

    cancellation_payload = {"status": "cancelled", "reason": "timeout"}
    history_store.append_step.assert_awaited_once_with("train-2", cancellation_payload)
    history_store.mark_cancelled.assert_awaited_once_with("train-2", reason="timeout")
    assert cancel_calls == ["timeout"]
    emit_update.assert_awaited_once_with(
        history_store,
        websocket,
        "train-2",
        include_root=True,
        complete=True,
    )
    safe_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_training_node_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send, emit_update = _setup_common_mocks(monkeypatch)
    _patch_graph_and_checkpointer(monkeypatch)
    error = RuntimeError("boom")
    _patch_agentensor_node(
        monkeypatch,
        "agentensor_trainer",
        exc=error,
    )
    history_store = _make_history_store()
    persist_history = AsyncMock()
    monkeypatch.setattr(workflow_execution, "_persist_failure_history", persist_history)
    failure_calls: list[Exception] = []
    span = SimpleNamespace()
    monkeypatch.setattr(
        workflow_execution,
        "record_workflow_failure",
        lambda span_arg, exc_arg: failure_calls.append(exc_arg),
    )
    websocket = object()

    with pytest.raises(RuntimeError):
        await _run_training_node(
            workflow_id="workflow-3",
            graph_config={},
            inputs={},
            runtime_config=SimpleNamespace(),
            state_config={},
            training_request=TrainingRequest(
                dataset=EvaluationDataset(
                    id="ds", cases=[EvaluationCase(inputs={"foo": "bar"})]
                )
            ),
            parsed_config=SimpleNamespace(
                prompts={"foo": "bar"},
                tags=[],
                callbacks=[],
                metadata={},
                run_name=None,
            ),
            history_store=history_store,
            websocket=websocket,
            execution_id="train-3",
            tracer=object(),
            resolver=object(),
            settings={},
            span=span,
            checkpoint_store=object(),
        )

    persist_history.assert_awaited_once_with(
        history_store,
        "train-3",
        {"status": "error", "error": "boom"},
        "boom",
        span,
    )
    emit_update.assert_awaited_once_with(
        history_store,
        websocket,
        "train-3",
        include_root=True,
        complete=True,
    )
    assert failure_calls == [error]
    safe_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_workflow_evaluation_rejects_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send = AsyncMock()
    monkeypatch.setattr(workflow_execution, "_safe_send_json", safe_send)

    await execute_workflow_evaluation(
        workflow_id="workflow",
        graph_config={},
        inputs={},
        execution_id="exec-eval",
        websocket=object(),
        evaluation={"dataset": {"cases": []}},
    )

    assert safe_send.await_count == 1
    payload = safe_send.await_args.args[1]
    assert payload["status"] == "error"
    assert "At least one evaluation case is required" in payload["error"]


@pytest.mark.asyncio
async def test_execute_workflow_training_rejects_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send = AsyncMock()
    monkeypatch.setattr(workflow_execution, "_safe_send_json", safe_send)

    await execute_workflow_training(
        workflow_id="workflow",
        graph_config={},
        inputs={},
        execution_id="exec-train",
        websocket=object(),
        training={"dataset": {"cases": []}},
    )

    assert safe_send.await_count == 1
    payload = safe_send.await_args.args[1]
    assert payload["status"] == "error"
    assert "At least one evaluation case is required" in payload["error"]


class _FakeSpanContext:
    def __init__(self) -> None:
        self.span = object()
        self.trace_id = "trace-id"
        self.started_at = datetime.now(tz=UTC)


@contextlib.contextmanager
def _fake_workflow_span(*args: Any, **kwargs: Any) -> Any:
    yield _FakeSpanContext()


@pytest.mark.asyncio
async def test_execute_workflow_evaluation_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send = AsyncMock()
    emit_update = AsyncMock()
    monkeypatch.setattr(workflow_execution, "_safe_send_json", safe_send)
    monkeypatch.setattr(workflow_execution, "_emit_trace_update", emit_update)

    history_store = AsyncMock()
    history_store.start_run = AsyncMock()
    history_store.append_step = AsyncMock(return_value=SimpleNamespace())
    history_store.mark_completed = AsyncMock()
    monkeypatch.setattr(workflow_execution, "get_history_store", lambda: history_store)
    monkeypatch.setattr(workflow_execution, "get_settings", lambda: {})
    monkeypatch.setattr(workflow_execution, "get_repository", lambda: object())
    monkeypatch.setattr(
        workflow_execution, "resolve_workflow_workspace_id", _echo_workspace_id
    )
    monkeypatch.setattr(workflow_execution, "get_vault", lambda: object())
    monkeypatch.setattr(
        workflow_execution,
        "credential_context_from_workflow",
        lambda workflow_id, workspace_id=None: {"workflow_id": workflow_id},
    )
    monkeypatch.setattr(
        workflow_execution,
        "CredentialResolver",
        lambda vault, context=None: object(),
    )
    monkeypatch.setattr(
        workflow_execution, "credential_resolution", contextlib.nullcontext
    )
    monkeypatch.setattr(workflow_execution, "get_tracer", lambda name: object())
    monkeypatch.setattr(workflow_execution, "workflow_span", _fake_workflow_span)
    runner = AsyncMock()
    monkeypatch.setattr(workflow_execution, "_run_evaluation_node", runner)
    monkeypatch.setattr(
        workflow_execution, "record_workflow_completion", lambda span: None
    )

    websocket = object()
    await execute_workflow_evaluation(
        workflow_id="workflow",
        graph_config={},
        inputs={},
        execution_id="exec-eval",
        websocket=websocket,
        evaluation={"dataset": {"cases": [{"inputs": {"foo": "bar"}}]}},
    )

    runner.assert_awaited_once()
    history_store.start_run.assert_awaited_once()
    safe_send.assert_any_await(websocket, {"status": "completed"})
    assert emit_update.await_args_list[-1][1]["complete"] is True


@pytest.mark.asyncio
async def test_execute_workflow_training_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_send = AsyncMock()
    emit_update = AsyncMock()
    monkeypatch.setattr(workflow_execution, "_safe_send_json", safe_send)
    monkeypatch.setattr(workflow_execution, "_emit_trace_update", emit_update)

    history_store = AsyncMock()
    history_store.start_run = AsyncMock()
    history_store.append_step = AsyncMock(return_value=SimpleNamespace())
    history_store.mark_completed = AsyncMock()
    monkeypatch.setattr(workflow_execution, "get_history_store", lambda: history_store)
    monkeypatch.setattr(workflow_execution, "get_settings", lambda: {})
    monkeypatch.setattr(workflow_execution, "get_checkpoint_store", lambda: object())
    monkeypatch.setattr(workflow_execution, "get_repository", lambda: object())
    monkeypatch.setattr(
        workflow_execution, "resolve_workflow_workspace_id", _echo_workspace_id
    )
    monkeypatch.setattr(workflow_execution, "get_vault", lambda: object())
    monkeypatch.setattr(
        workflow_execution,
        "credential_context_from_workflow",
        lambda workflow_id, workspace_id=None: {"workflow_id": workflow_id},
    )
    monkeypatch.setattr(
        workflow_execution,
        "CredentialResolver",
        lambda vault, context=None: object(),
    )
    monkeypatch.setattr(
        workflow_execution, "credential_resolution", contextlib.nullcontext
    )
    monkeypatch.setattr(workflow_execution, "get_tracer", lambda name: object())
    monkeypatch.setattr(workflow_execution, "workflow_span", _fake_workflow_span)
    runner = AsyncMock()
    monkeypatch.setattr(workflow_execution, "_run_training_node", runner)
    monkeypatch.setattr(
        workflow_execution, "record_workflow_completion", lambda span: None
    )

    websocket = object()
    await execute_workflow_training(
        workflow_id="workflow",
        graph_config={},
        inputs={},
        execution_id="exec-train",
        websocket=websocket,
        training={"dataset": {"cases": [{"inputs": {"foo": "bar"}}]}},
    )

    runner.assert_awaited_once()
    history_store.start_run.assert_awaited_once()
    safe_send.assert_any_await(websocket, {"status": "completed"})
    assert emit_update.await_args_list[-1][1]["complete"] is True
