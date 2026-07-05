"""Tests covering websocket streaming for workflow executions."""

from __future__ import annotations
import json
import sys
from types import ModuleType
from typing import Any, cast
import pytest
from orcheo_sdk.cli import workflow as workflow_module
from orcheo_sdk.cli.state import CLIState
from orcheo_sdk.cli.workflow import _stream_workflow_run
from tests.sdk.workflow_cli_test_utils import make_state


@pytest.fixture()
def fake_websockets(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = ModuleType("websockets")
    exceptions_module = ModuleType("websockets.exceptions")

    class InvalidStatusCodeError(Exception):
        def __init__(self, status_code: int) -> None:
            super().__init__(status_code)
            self.status_code = status_code

    class WebSocketExceptionError(Exception):
        pass

    exceptions_module.InvalidStatusCode = InvalidStatusCodeError
    exceptions_module.WebSocketException = WebSocketExceptionError
    module.exceptions = exceptions_module

    def default_connect(*_: Any, **__: Any) -> Any:
        raise RuntimeError("connect stub not configured")

    module.connect = default_connect  # type: ignore[assignment]

    monkeypatch.setitem(sys.modules, "websockets", module)
    monkeypatch.setitem(sys.modules, "websockets.exceptions", exceptions_module)
    return module


@pytest.mark.asyncio()
async def test_stream_workflow_run_includes_additional_headers(
    fake_websockets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = make_state()

    class DummyConnection:
        async def __aenter__(self) -> DummyConnection:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def send(self, _: str) -> None:
            return None

    connection = DummyConnection()

    def fake_connect(
        uri: str,
        *,
        open_timeout: float,
        close_timeout: float,
        max_size: int | None,
        additional_headers: dict[str, str] | None = None,
    ) -> DummyConnection:
        assert uri.endswith("/ws/workflow/wf-1")
        assert open_timeout == 5
        assert close_timeout == 5
        assert max_size is None
        assert additional_headers == {"Authorization": "Bearer token"}
        return connection

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    async def fake_process(_: CLIState, __: Any) -> str:
        return "completed"

    monkeypatch.setattr(
        "orcheo_sdk.cli.workflow._process_stream_messages", fake_process
    )

    result = await _stream_workflow_run(
        state,
        "wf-1",
        {"nodes": []},
        {"input": "value"},
    )
    assert result == "completed"


@pytest.mark.asyncio()
async def test_stream_workflow_run_falls_back_to_extra_headers(
    fake_websockets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = make_state()

    class DummyConnection:
        async def __aenter__(self) -> DummyConnection:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def send(self, _: str) -> None:
            return None

    connection = DummyConnection()

    def fake_connect(
        uri: str,
        *,
        open_timeout: float,
        close_timeout: float,
        max_size: int | None,
        additional_headers: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> DummyConnection:
        assert uri.endswith("/ws/workflow/wf-1")
        assert open_timeout == 5
        assert close_timeout == 5
        assert max_size is None
        if additional_headers is not None:
            raise TypeError("unexpected keyword argument 'additional_headers'")
        assert extra_headers == {"Authorization": "Bearer token"}
        return connection

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    async def fake_process(_: CLIState, __: Any) -> str:
        return "completed"

    monkeypatch.setattr(
        "orcheo_sdk.cli.workflow._process_stream_messages", fake_process
    )

    result = await _stream_workflow_run(
        state,
        "wf-1",
        {"nodes": []},
        {"input": "value"},
    )
    assert result == "completed"


@pytest.mark.asyncio()
async def test_stream_workflow_run_without_headers(
    fake_websockets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = make_state()
    monkeypatch.setattr(state.client, "get_active_token", lambda: None)

    class DummyConnection:
        async def __aenter__(self) -> DummyConnection:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def send(self, _: str) -> None:
            return None

    connection = DummyConnection()

    def fake_connect(
        uri: str,
        *,
        open_timeout: float,
        close_timeout: float,
        max_size: int | None,
    ) -> DummyConnection:
        assert uri.endswith("/ws/workflow/wf-1")
        assert open_timeout == 5
        assert close_timeout == 5
        assert max_size is None
        return connection

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    async def fake_process(_: CLIState, __: Any) -> str:
        return "completed"

    monkeypatch.setattr(
        "orcheo_sdk.cli.workflow._process_stream_messages", fake_process
    )

    result = await _stream_workflow_run(
        state,
        "wf-1",
        {"nodes": []},
        {"input": "value"},
    )
    assert result == "completed"


@pytest.mark.asyncio()
async def test_stream_workflow_run_succeeds(
    fake_websockets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = make_state()

    class DummyConnection:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def __aenter__(self) -> DummyConnection:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def send(self, message: str) -> None:
            self.sent.append(message)

    connection = DummyConnection()

    async def fake_process(state_arg: CLIState, websocket: Any) -> str:
        assert state_arg is state
        assert websocket is connection
        return "completed"

    def fake_connect(*_: Any, **__: Any) -> DummyConnection:
        return connection

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "orcheo_sdk.cli.workflow._process_stream_messages", fake_process
    )

    result = await _stream_workflow_run(
        state,
        "wf-1",
        {"nodes": []},
        {"input": "value"},
        triggered_by="cli-actor",
        runnable_config={"priority": "high"},
        stored_runnable_config={"tags": ["stored"]},
    )
    assert result == "completed"
    assert connection.sent, "payload was not sent"
    payload = json.loads(connection.sent[0])
    assert payload["type"] == "run_workflow"
    assert payload["inputs"] == {"input": "value"}
    assert payload["triggered_by"] == "cli-actor"
    assert payload["runnable_config"] == {"priority": "high"}
    assert payload["stored_runnable_config"] == {"tags": ["stored"]}


@pytest.mark.asyncio()
async def test_stream_workflow_run_machine_verbose_emits_start_json(
    fake_websockets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = make_state(verbose_results=True)
    state.human = False
    emitted: list[dict[str, Any]] = []

    class DummyConnection:
        async def __aenter__(self) -> DummyConnection:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def send(self, _: str) -> None:
            return None

    connection = DummyConnection()

    def fake_connect(*_: Any, **__: Any) -> DummyConnection:
        return connection

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    async def fake_process(_: CLIState, __: Any) -> str:
        return "completed"

    def fake_print_json(data: Any) -> None:
        assert isinstance(data, dict)
        emitted.append(data)

    monkeypatch.setattr(
        "orcheo_sdk.cli.workflow._process_stream_messages", fake_process
    )
    monkeypatch.setattr("orcheo_sdk.cli.workflow.streaming.print_json", fake_print_json)

    result = await _stream_workflow_run(
        state,
        "wf-1",
        {"nodes": []},
        {"input": "value"},
    )
    assert result == "completed"
    assert emitted[0]["event"] == "workflow.execution.start"
    assert emitted[0]["workflow_id"] == "wf-1"
    assert not state.console.messages


@pytest.mark.asyncio()
async def test_stream_workflow_run_handles_connection_error(
    fake_websockets: ModuleType,
) -> None:
    state = make_state()

    def fake_connect(*_: Any, **__: Any) -> Any:
        raise ConnectionRefusedError("no route")

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    result = await _stream_workflow_run(
        state,
        "wf-1",
        {"cfg": True},
        {},
        triggered_by=None,
    )
    assert result == "connection_error"
    assert any("Failed to connect" in msg for msg in state.console.messages)


@pytest.mark.asyncio()
async def test_stream_workflow_run_handles_timeout(
    fake_websockets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = make_state()

    class FakeTimeoutError(Exception):
        pass

    monkeypatch.setattr(
        workflow_module, "TimeoutError", FakeTimeoutError, raising=False
    )

    def fake_connect(*_: Any, **__: Any) -> Any:
        raise FakeTimeoutError()

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    result = await _stream_workflow_run(
        state,
        "wf-1",
        {"cfg": True},
        {},
        triggered_by=None,
    )
    assert result == "timeout"
    assert any("Timed out" in msg for msg in state.console.messages)


@pytest.mark.asyncio()
async def test_stream_workflow_run_handles_invalid_status(
    fake_websockets: ModuleType,
) -> None:
    state = make_state()

    invalid_status = cast(
        type[Exception],
        fake_websockets.exceptions.InvalidStatusCode,
    )

    def fake_connect(*_: Any, **__: Any) -> Any:
        raise invalid_status(403)

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    result = await _stream_workflow_run(
        state,
        "wf-1",
        {"cfg": True},
        {},
        triggered_by=None,
    )
    assert result == "http_403"
    assert any("Server rejected connection" in msg for msg in state.console.messages)


@pytest.mark.asyncio()
async def test_stream_workflow_run_handles_websocket_exception(
    fake_websockets: ModuleType,
) -> None:
    state = make_state()

    ws_error = cast(
        type[Exception],
        fake_websockets.exceptions.WebSocketException,
    )

    def fake_connect(*_: Any, **__: Any) -> Any:
        raise ws_error("crash")

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    result = await _stream_workflow_run(
        state,
        "wf-1",
        {"cfg": True},
        {},
        triggered_by=None,
    )
    assert result == "websocket_error"
    assert any("WebSocket error" in msg for msg in state.console.messages)


@pytest.mark.asyncio()
async def test_stream_workflow_evaluation_succeeds(
    fake_websockets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = make_state()

    class DummyConnection:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def __aenter__(self) -> DummyConnection:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def send(self, message: str) -> None:
            self.sent.append(message)

    connection = DummyConnection()

    async def fake_process(state_arg: CLIState, websocket: Any) -> str:
        assert state_arg is state
        assert websocket is connection
        return "completed"

    def fake_connect(*_: Any, **__: Any) -> DummyConnection:
        return connection

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "orcheo_sdk.cli.workflow._process_stream_messages", fake_process
    )

    result = await workflow_module._stream_workflow_evaluation(
        state,
        "wf-1",
        {"nodes": []},
        {"input": "value"},
        {"name": "agent"},
        triggered_by="cli-actor",
        runnable_config={"priority": "high"},
        stored_runnable_config={"tags": ["stored"]},
    )
    assert result == "completed"
    assert connection.sent, "payload was not sent"
    payload = json.loads(connection.sent[0])
    assert payload["type"] == "evaluate_workflow"
    assert payload["evaluation"] == {"name": "agent"}
    assert payload["runnable_config"] == {"priority": "high"}
    assert payload["stored_runnable_config"] == {"tags": ["stored"]}


@pytest.mark.asyncio()
async def test_stream_workflow_evaluation_machine_verbose_emits_start_json(
    fake_websockets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = make_state(verbose_results=True)
    state.human = False
    emitted: list[dict[str, Any]] = []

    class DummyConnection:
        async def __aenter__(self) -> DummyConnection:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def send(self, _: str) -> None:
            return None

    connection = DummyConnection()

    def fake_connect(*_: Any, **__: Any) -> DummyConnection:
        return connection

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    async def fake_process(_: CLIState, __: Any) -> str:
        return "completed"

    def fake_print_json(data: Any) -> None:
        assert isinstance(data, dict)
        emitted.append(data)

    monkeypatch.setattr(
        "orcheo_sdk.cli.workflow._process_stream_messages", fake_process
    )
    monkeypatch.setattr("orcheo_sdk.cli.workflow.streaming.print_json", fake_print_json)

    result = await workflow_module._stream_workflow_evaluation(
        state,
        "wf-1",
        {"nodes": []},
        {"input": "value"},
        {"name": "agent"},
    )
    assert result == "completed"
    assert emitted[0]["event"] == "workflow.evaluation.start"
    assert emitted[0]["workflow_id"] == "wf-1"
    assert not state.console.messages


@pytest.mark.asyncio()
async def test_stream_workflow_evaluation_handles_connection_error(
    fake_websockets: ModuleType,
) -> None:
    state = make_state()

    def fake_connect(*_: Any, **__: Any) -> Any:
        raise ConnectionRefusedError("no route")

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    result = await workflow_module._stream_workflow_evaluation(
        state,
        "wf-1",
        {"cfg": True},
        {},
        {},
        triggered_by=None,
    )
    assert result == "connection_error"
    assert any("Failed to connect" in msg for msg in state.console.messages)


@pytest.mark.asyncio()
async def test_stream_workflow_evaluation_handles_timeout(
    fake_websockets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = make_state()

    class FakeTimeoutError(Exception):
        pass

    monkeypatch.setattr(
        workflow_module, "TimeoutError", FakeTimeoutError, raising=False
    )

    def fake_connect(*_: Any, **__: Any) -> Any:
        raise FakeTimeoutError()

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    result = await workflow_module._stream_workflow_evaluation(
        state,
        "wf-1",
        {"cfg": True},
        {},
        {},
        triggered_by=None,
    )
    assert result == "timeout"
    assert any("Timed out" in msg for msg in state.console.messages)


@pytest.mark.asyncio()
async def test_stream_workflow_evaluation_handles_invalid_status(
    fake_websockets: ModuleType,
) -> None:
    state = make_state()

    invalid_status = cast(
        type[Exception],
        fake_websockets.exceptions.InvalidStatusCode,
    )

    def fake_connect(*_: Any, **__: Any) -> Any:
        raise invalid_status(403)

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    result = await workflow_module._stream_workflow_evaluation(
        state,
        "wf-1",
        {"cfg": True},
        {},
        {},
        triggered_by=None,
    )
    assert result == "http_403"
    assert any("Server rejected connection" in msg for msg in state.console.messages)


@pytest.mark.asyncio()
async def test_stream_workflow_evaluation_handles_websocket_exception(
    fake_websockets: ModuleType,
) -> None:
    state = make_state()

    ws_error = cast(
        type[Exception],
        fake_websockets.exceptions.WebSocketException,
    )

    def fake_connect(*_: Any, **__: Any) -> Any:
        raise ws_error("crash")

    fake_websockets.connect = fake_connect  # type: ignore[attr-defined]

    result = await workflow_module._stream_workflow_evaluation(
        state,
        "wf-1",
        {"cfg": True},
        {},
        {},
        triggered_by=None,
    )
    assert result == "websocket_error"
    assert any("WebSocket error" in msg for msg in state.console.messages)


def test_handle_trace_update_with_empty_spans_and_complete_flag() -> None:
    """Test _handle_trace_update with empty spans and complete=True (lines 305-307)."""
    from orcheo_sdk.cli.workflow.streaming import _handle_trace_update

    state = make_state()
    update = {"type": "trace:update", "spans": [], "complete": True}
    _handle_trace_update(state, update)
    assert any("Trace update: complete" in msg for msg in state.console.messages)


def test_handle_trace_update_with_non_list_spans_and_complete_flag() -> None:
    """Test _handle_trace_update with non-list spans and complete=True."""
    from orcheo_sdk.cli.workflow.streaming import _handle_trace_update

    state = make_state()
    update = {"type": "trace:update", "spans": None, "complete": True}
    _handle_trace_update(state, update)
    assert any("Trace update: complete" in msg for msg in state.console.messages)


def test_handle_trace_update_with_non_dict_last_span() -> None:
    """Test _handle_trace_update when last_span is not a dict (line 311)."""
    from orcheo_sdk.cli.workflow.streaming import _handle_trace_update

    state = make_state()
    update = {"type": "trace:update", "spans": ["not-a-dict"]}
    _handle_trace_update(state, update)
    # Should return early without printing anything
    assert not state.console.messages


def test_handle_trace_update_with_non_dict_status() -> None:
    """Test _handle_trace_update when status is not a dict (lines 317->321, 327)."""
    from orcheo_sdk.cli.workflow.streaming import _handle_trace_update

    state = make_state()
    # status is a string, not a dict, so status_code and status_message remain None
    update = {"type": "trace:update", "spans": [{"name": "test", "status": "ok"}]}
    _handle_trace_update(state, update)
    # Should print just the name without status info
    assert any("Trace update: test" in msg for msg in state.console.messages)


def test_handle_trace_update_with_status_message() -> None:
    """Test _handle_trace_update with status_message (line 323)."""
    from orcheo_sdk.cli.workflow.streaming import _handle_trace_update

    state = make_state()
    update = {
        "type": "trace:update",
        "spans": [
            {
                "name": "workflow",
                "status": {"code": "OK", "message": "Success!"},
            }
        ],
    }
    _handle_trace_update(state, update)
    # Note: strip() removes the leading space from status_text
    assert any(
        "Trace update: workflow(OK) Success!" in msg for msg in state.console.messages
    )


def test_handle_trace_update_with_status_code_only() -> None:
    """Test _handle_trace_update with status_code but no message."""
    from orcheo_sdk.cli.workflow.streaming import _handle_trace_update

    state = make_state()
    update = {
        "type": "trace:update",
        "spans": [{"name": "workflow", "status": {"code": "ERROR"}}],
    }
    _handle_trace_update(state, update)
    assert any(
        "Trace update: workflow (ERROR)" in msg for msg in state.console.messages
    )


def test_handle_generic_update_with_empty_update() -> None:
    """Test _handle_generic_update with empty update dict (line 333)."""
    from orcheo_sdk.cli.workflow.streaming import _handle_generic_update

    state = make_state()
    _handle_generic_update(state, {})
    # Should return early without printing
    assert not state.console.messages


def test_handle_generic_update_with_non_dict_payload() -> None:
    """Test _handle_generic_update with single key and non-dict payload."""
    from orcheo_sdk.cli.workflow.streaming import _handle_generic_update

    state = make_state()
    update = {"node_name": "string_payload"}
    _handle_generic_update(state, update)
    assert any("• node_name" in msg for msg in state.console.messages)


def test_handle_generic_update_with_empty_dict_payload() -> None:
    """Test _handle_generic_update with single key and empty dict payload."""
    from orcheo_sdk.cli.workflow.streaming import _handle_generic_update

    state = make_state()
    update = {"node_name": {}}
    _handle_generic_update(state, update)
    # Empty dict has no keys, so detail should be empty
    assert any("• node_name" in msg for msg in state.console.messages)


def test_handle_generic_update_with_node_results_payload() -> None:
    """Test _handle_generic_update with top-level node_results payload."""
    from orcheo_sdk.cli.workflow.streaming import _handle_generic_update

    state = make_state(verbose_results=True)
    update = {"node_results": {"node_name": {"value": 1}}}
    _handle_generic_update(state, update)
    assert any("Results" in msg for msg in state.console.messages)


def test_handle_generic_update_with_node_scoped_node_results_payload() -> None:
    """Test _handle_generic_update with node-scoped node_results payload."""
    from orcheo_sdk.cli.workflow.streaming import _handle_generic_update

    state = make_state(verbose_results=True)
    update = {"node_name": {"node_results": {"node_name": {"value": 2}}}}
    _handle_generic_update(state, update)
    assert any("node_name results" in msg for msg in state.console.messages)


def test_handle_generic_update_with_payload_more_than_four_keys() -> None:
    """Test _handle_generic_update with >4 keys in payload (line 342)."""
    from orcheo_sdk.cli.workflow.streaming import _handle_generic_update

    state = make_state()
    update = {"node_name": {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}}
    _handle_generic_update(state, update)
    # Should show first 4 keys with ellipsis
    assert any("• node_name (a, b, c, d, …)" in msg for msg in state.console.messages)


def test_handle_generic_update_with_multiple_update_keys() -> None:
    """Test _handle_generic_update with multiple keys (lines 346-351)."""
    from orcheo_sdk.cli.workflow.streaming import _handle_generic_update

    state = make_state()
    update = {"key1": "val1", "key2": "val2"}
    _handle_generic_update(state, update)
    assert any("Update keys: key1, key2" in msg for msg in state.console.messages)


def test_handle_generic_update_with_more_than_four_update_keys() -> None:
    """Test _handle_generic_update with >4 update keys (lines 349-350)."""
    from orcheo_sdk.cli.workflow.streaming import _handle_generic_update

    state = make_state()
    update = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
    _handle_generic_update(state, update)
    assert any("Update keys: a, b, c, d, …" in msg for msg in state.console.messages)


def test_handle_trace_update_empty_spans_without_complete() -> None:
    """Test _handle_trace_update with empty spans and no complete flag."""
    from orcheo_sdk.cli.workflow.streaming import _handle_trace_update

    state = make_state()
    update = {"type": "trace:update", "spans": []}
    _handle_trace_update(state, update)
    # Should return early without printing anything (complete flag not set)
    assert not state.console.messages


def test_handle_generic_update_with_payload_few_keys() -> None:
    """Test _handle_generic_update with 1-4 keys in payload (branch 341->343)."""
    from orcheo_sdk.cli.workflow.streaming import _handle_generic_update

    state = make_state()
    update = {"node_name": {"key1": 1, "key2": 2}}
    _handle_generic_update(state, update)
    # Should show keys without ellipsis since <= 4 keys
    assert any("• node_name (key1, key2)" in msg for msg in state.console.messages)


def test_handle_single_update_verbose_results_early_return() -> None:
    """Test _handle_single_update returns early when verbose_results renders results."""
    from orcheo_sdk.cli.workflow.streaming import _handle_single_update

    state = make_state(verbose_results=True)
    payload = {"node_results": {"my_node": {"score": 0.95}}}
    _handle_single_update(state, "my_node", payload)
    # _render_results_payload renders JSON and returns True, so the
    # "• my_node" fallback line should NOT appear.
    assert not any("• my_node" in msg for msg in state.console.messages)
    # But results should have been rendered
    assert state.console.messages
