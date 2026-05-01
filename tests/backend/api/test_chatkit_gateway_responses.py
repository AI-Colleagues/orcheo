"""ChatKit gateway response handling tests for orcheo_backend.app."""

from __future__ import annotations
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.backend.api.shared import backend_app


@pytest.mark.asyncio
async def test_chatkit_gateway_validation_error(api_client: TestClient) -> None:
    """chatkit_gateway rejects malformed payloads."""
    response = api_client.post(
        "/api/chatkit",
        json={"invalid": "payload"},
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Invalid ChatKit payload" in detail["message"]
    assert "errors" in detail


@pytest.mark.asyncio
async def test_chatkit_gateway_invalid_json_payload(api_client: TestClient) -> None:
    """chatkit_gateway surfaces JSON decoding failures."""
    response = api_client.post(
        "/api/chatkit",
        content="{invalid",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert "Invalid JSON payload" in response.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_chatkit_gateway_rejects_non_object_payload(
    api_client: TestClient,
) -> None:
    """chatkit_gateway returns 400 when JSON payload is not an object."""
    response = api_client.post(
        "/api/chatkit",
        json=["not", "an", "object"],
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "Invalid ChatKit payload."
    assert "JSON object" in detail["errors"][0]


@pytest.mark.asyncio
async def test_chatkit_gateway_streaming_response(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chatkit_gateway handles streaming responses."""
    from chatkit.server import StreamingResult

    async def mock_stream() -> AsyncIterator[bytes]:
        yield b"data: test\n\n"

    mock_result = StreamingResult(mock_stream())

    async def mock_process(payload: bytes, context: Any) -> StreamingResult:
        return mock_result

    mock_server = AsyncMock()
    mock_server.process = mock_process

    mock_adapter = Mock()
    mock_adapter.validate_python.return_value = {"action": "chat"}

    auth_result = backend_app.routers.chatkit.ChatKitAuthResult(
        workflow_id=uuid4(),
        actor="jwt:tester",
        auth_mode="jwt",
        subject="tester",
    )

    monkeypatch.setattr(backend_app, "get_chatkit_server", lambda: mock_server)
    monkeypatch.setattr(backend_app, "TypeAdapter", lambda x: mock_adapter)
    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )

    response = api_client.post(
        "/api/chatkit",
        json={"test": "payload", "workflow_id": str(auth_result.workflow_id)},
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_chatkit_gateway_json_response_with_callable(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chatkit_gateway supports callables returning JSON responses."""

    class MockResult:
        def __init__(self) -> None:
            self.json = lambda: {"result": "success"}
            self.status_code = 200
            self.headers = {"x-custom": "header"}
            self.media_type = "application/json"

    async def mock_process(payload: bytes, context: Any) -> MockResult:
        return MockResult()

    mock_server = AsyncMock()
    mock_server.process = mock_process

    mock_adapter = Mock()
    mock_adapter.validate_python.return_value = {"action": "chat"}

    auth_result = backend_app.routers.chatkit.ChatKitAuthResult(
        workflow_id=uuid4(),
        actor="jwt:tester",
        auth_mode="jwt",
        subject="tester",
    )

    monkeypatch.setattr(backend_app, "get_chatkit_server", lambda: mock_server)
    monkeypatch.setattr(backend_app, "TypeAdapter", lambda x: mock_adapter)
    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )

    response = api_client.post(
        "/api/chatkit",
        json={"test": "payload", "workflow_id": str(auth_result.workflow_id)},
    )

    assert response.status_code == 200
    assert response.json() == {"result": "success"}


@pytest.mark.asyncio
async def test_chatkit_gateway_accepts_top_level_workflow_id(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ChatKit gateway strips workflow_id before schema validation."""

    workflow_id = uuid4()

    async def mock_process(payload: bytes, context: Any) -> dict[str, str]:
        assert b"workflow_id" not in payload
        assert context["workflow_id"] == str(workflow_id)
        return {"result": "ok"}

    mock_server = AsyncMock()
    mock_server.process = mock_process

    def mock_validate_python(payload: Any) -> dict[str, Any]:
        assert "workflow_id" not in payload
        return {"type": "threads.add_user_message", "params": {}}

    mock_adapter = Mock()
    mock_adapter.validate_python.side_effect = mock_validate_python

    auth_result = backend_app.routers.chatkit.ChatKitAuthResult(
        workflow_id=workflow_id,
        actor="jwt:tester",
        auth_mode="jwt",
        subject="tester",
    )

    monkeypatch.setattr(backend_app, "get_chatkit_server", lambda: mock_server)
    monkeypatch.setattr(backend_app, "TypeAdapter", lambda x: mock_adapter)
    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )

    response = api_client.post(
        "/api/chatkit",
        json={
            "workflow_id": str(workflow_id),
            "type": "threads.add_user_message",
            "params": {},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"result": "ok"}


@pytest.mark.asyncio
async def test_chatkit_gateway_accepts_top_level_workflow_id_camel_case(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ChatKit gateway also accepts the camelCase workflowId variant."""

    workflow_id = uuid4()

    async def mock_process(payload: bytes, context: Any) -> dict[str, str]:
        assert b"workflowId" not in payload
        assert b"workflow_id" not in payload
        assert context["workflow_id"] == str(workflow_id)
        return {"result": "ok"}

    mock_server = AsyncMock()
    mock_server.process = mock_process

    def mock_validate_python(payload: Any) -> dict[str, Any]:
        assert "workflowId" not in payload
        assert "workflow_id" not in payload
        return {"type": "threads.add_user_message", "params": {}}

    mock_adapter = Mock()
    mock_adapter.validate_python.side_effect = mock_validate_python

    auth_result = backend_app.routers.chatkit.ChatKitAuthResult(
        workflow_id=workflow_id,
        actor="jwt:tester",
        auth_mode="jwt",
        subject="tester",
    )

    monkeypatch.setattr(backend_app, "get_chatkit_server", lambda: mock_server)
    monkeypatch.setattr(backend_app, "TypeAdapter", lambda x: mock_adapter)
    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )

    response = api_client.post(
        "/api/chatkit",
        json={
            "workflowId": str(workflow_id),
            "type": "threads.add_user_message",
            "params": {},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"result": "ok"}


@pytest.mark.asyncio
async def test_chatkit_gateway_json_response_with_bytes(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chatkit_gateway supports responses returning bytes."""

    class MockResult:
        def __init__(self) -> None:
            self.json = b"binary-data"
            self.status_code = 200
            self.headers = None
            self.media_type = "application/octet-stream"

    async def mock_process(payload: bytes, context: Any) -> MockResult:
        return MockResult()

    mock_server = AsyncMock()
    mock_server.process = mock_process

    mock_adapter = Mock()
    mock_adapter.validate_python.return_value = {"action": "chat"}

    auth_result = backend_app.routers.chatkit.ChatKitAuthResult(
        workflow_id=uuid4(),
        actor="jwt:tester",
        auth_mode="jwt",
        subject="tester",
    )

    monkeypatch.setattr(backend_app, "get_chatkit_server", lambda: mock_server)
    monkeypatch.setattr(backend_app, "TypeAdapter", lambda x: mock_adapter)
    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )

    response = api_client.post(
        "/api/chatkit",
        json={"test": "payload", "workflow_id": str(auth_result.workflow_id)},
    )

    assert response.status_code == 200
    assert response.content == b"binary-data"


@pytest.mark.asyncio
async def test_chatkit_gateway_json_response_with_string(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chatkit_gateway supports string payloads."""

    class MockResult:
        def __init__(self) -> None:
            self.json = "text response"
            self.status_code = 200
            self.headers = [("x-custom", "value")]
            self.media_type = "text/plain"

    async def mock_process(payload: bytes, context: Any) -> MockResult:
        return MockResult()

    mock_server = AsyncMock()
    mock_server.process = mock_process

    mock_adapter = Mock()
    mock_adapter.validate_python.return_value = {"action": "chat"}

    auth_result = backend_app.routers.chatkit.ChatKitAuthResult(
        workflow_id=uuid4(),
        actor="jwt:tester",
        auth_mode="jwt",
        subject="tester",
    )

    monkeypatch.setattr(backend_app, "get_chatkit_server", lambda: mock_server)
    monkeypatch.setattr(backend_app, "TypeAdapter", lambda x: mock_adapter)
    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )

    response = api_client.post(
        "/api/chatkit",
        json={"test": "payload", "workflow_id": str(auth_result.workflow_id)},
    )

    assert response.status_code == 200
    assert response.text == "text response"


@pytest.mark.asyncio
async def test_chatkit_gateway_dict_response(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chatkit_gateway handles raw dict responses."""

    async def mock_process(payload: bytes, context: Any) -> dict[str, str]:
        return {"status": "ok"}

    mock_server = AsyncMock()
    mock_server.process = mock_process

    mock_adapter = Mock()
    mock_adapter.validate_python.return_value = {"action": "chat"}

    auth_result = backend_app.routers.chatkit.ChatKitAuthResult(
        workflow_id=uuid4(),
        actor="jwt:tester",
        auth_mode="jwt",
        subject="tester",
    )

    monkeypatch.setattr(backend_app, "get_chatkit_server", lambda: mock_server)
    monkeypatch.setattr(backend_app, "TypeAdapter", lambda x: mock_adapter)
    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )

    response = api_client.post(
        "/api/chatkit",
        json={"test": "payload", "workflow_id": str(auth_result.workflow_id)},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_chatkit_gateway_omits_subject_when_not_provided(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chatkit_gateway excludes the subject when authentication omits it."""

    captured_context: list[dict[str, Any]] = []

    class MockResult:
        def __init__(self) -> None:
            self.json = {"status": "ok"}
            self.status_code = 200
            self.headers = None
            self.media_type = "application/json"

    async def mock_process(payload: bytes, context: Any) -> MockResult:
        captured_context.append(context)
        return MockResult()

    mock_server = AsyncMock()
    mock_server.process = mock_process

    mock_adapter = Mock()
    mock_adapter.validate_python.return_value = {"action": "chat"}

    auth_result = backend_app.routers.chatkit.ChatKitAuthResult(
        workflow_id=uuid4(),
        actor="publish:token",
        auth_mode="publish",
        subject=None,
    )

    monkeypatch.setattr(backend_app, "get_chatkit_server", lambda: mock_server)
    monkeypatch.setattr(backend_app, "TypeAdapter", lambda x: mock_adapter)
    monkeypatch.setattr(
        backend_app.routers.chatkit,
        "authenticate_chatkit_invocation",
        AsyncMock(return_value=auth_result),
    )

    response = api_client.post(
        "/api/chatkit",
        json={"test": "payload", "workflow_id": str(auth_result.workflow_id)},
    )

    assert response.status_code == 200
    assert captured_context
    assert "subject" not in captured_context[0]
