import pytest
from fastapi import WebSocketDisconnect
from orcheo_backend.app import workflow_execution
from orcheo_backend.app.workflow_execution import (
    _CANNOT_SEND_AFTER_CLOSE,
    _json_safe_value,
    _safe_send_json,
    _sanitize_public_step_payload,
)


class DummyWebSocket:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def send_json(
        self, payload: object
    ) -> None:  # pragma: no cover - exceptions handled
        raise self._exc


def test_sanitize_public_step_payload_returns_dict():
    sanitized = _sanitize_public_step_payload({"foo": "bar"})
    assert isinstance(sanitized, dict)
    assert sanitized["foo"] == "bar"


def test_sanitize_public_step_payload_falls_back_for_non_mapping(monkeypatch):
    monkeypatch.setattr(
        workflow_execution,
        "strip_trace_metadata",
        lambda payload: ["not", "a", "mapping"],
    )

    sanitized = _sanitize_public_step_payload({"foo": "bar"})

    assert sanitized == {"foo": "bar"}


def test_json_safe_value_handles_interrupt_like_objects() -> None:
    class Interrupt:
        def __init__(self) -> None:
            self.value = {"reason": "stop"}
            self.id = "abc"

    assert _json_safe_value(Interrupt()) == {
        "value": {"reason": "stop"},
        "id": "abc",
    }


def test_json_safe_value_handles_interrupt_without_id_and_falls_back_to_string() -> (
    None
):
    class Interrupt:
        def __init__(self) -> None:
            self.value = "stop"

    assert _json_safe_value(Interrupt()) == {"value": "stop"}
    assert isinstance(_json_safe_value(object()), str)


@pytest.mark.asyncio
async def test_safe_send_json_handles_disconnect(monkeypatch):
    websocket = DummyWebSocket(WebSocketDisconnect(code=1000))
    result = await _safe_send_json(websocket, {"status": "ok"})
    assert result is False


@pytest.mark.asyncio
async def test_safe_send_json_handles_closed(monkeypatch):
    socket = DummyWebSocket(RuntimeError(_CANNOT_SEND_AFTER_CLOSE))
    result = await _safe_send_json(socket, {"status": "ok"})
    assert result is False
