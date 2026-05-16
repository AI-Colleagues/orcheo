"""Tests for HTTP executor retry, transport, and error recovery behavior."""

from __future__ import annotations
from collections.abc import Mapping
import json
import httpx
import pytest
from orcheo_sdk import (
    HttpWorkflowExecutor,
    OrcheoClient,
)


def test_http_executor_retries_and_sets_auth_header() -> None:
    captured_delays: list[float] = []

    def capture_delay(delay: float) -> None:
        captured_delays.append(delay)

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500)
        return httpx.Response(
            201,
            json={
                "id": "run-123",
                "status": "pending",
                "triggered_by": "tester",
                "input_payload": {"foo": "bar"},
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="http://localhost")
    executor = HttpWorkflowExecutor(
        client=OrcheoClient(base_url="http://localhost"),
        http_client=http_client,
        auth_token="secret",
        max_retries=2,
        backoff_factor=0.2,
        sleep=capture_delay,
    )

    try:
        payload = executor.trigger_run(
            "workflow",
            workflow_version_id="version",
            triggered_by="tester",
            inputs={"foo": "bar"},
        )
    finally:
        http_client.close()

    assert len(calls) == 2
    assert calls[0].headers.get("Authorization") == "Bearer secret"
    assert payload["status"] == "pending"
    assert captured_delays == [0.2]


def test_http_executor_raises_after_exhausting_retries() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="http://localhost")
    executor = HttpWorkflowExecutor(
        client=OrcheoClient(base_url="http://localhost"),
        http_client=http_client,
        max_retries=1,
        backoff_factor=0.0,
        sleep=lambda _delay: None,
    )

    try:
        with pytest.raises(RuntimeError) as exc_info:
            executor.trigger_run(
                "workflow",
                workflow_version_id="version",
                triggered_by="tester",
            )
    finally:
        http_client.close()

    assert attempts == 2
    assert "status 503" in str(exc_info.value)


def test_http_executor_recovers_from_transport_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(
            201,
            json={
                "id": "run-456",
                "status": "pending",
                "triggered_by": "tester",
                "input_payload": {},
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="http://localhost")
    executor = HttpWorkflowExecutor(
        client=OrcheoClient(base_url="http://localhost"),
        http_client=http_client,
        max_retries=1,
        backoff_factor=0.0,
    )

    try:
        payload = executor.trigger_run(
            "workflow",
            workflow_version_id="version",
            triggered_by="tester",
        )
    finally:
        http_client.close()

    assert attempts == 2
    assert payload["status"] == "pending"


def test_http_executor_raises_on_persistent_transport_error() -> None:
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(
            httpx.ConnectError("boom", request=request)
        )
    )
    http_client = httpx.Client(transport=transport, base_url="http://localhost")
    executor = HttpWorkflowExecutor(
        client=OrcheoClient(base_url="http://localhost"),
        http_client=http_client,
        max_retries=0,
        backoff_factor=0.0,
    )

    try:
        with pytest.raises(RuntimeError) as exc_info:
            executor.trigger_run(
                "workflow",
                workflow_version_id="version",
                triggered_by="tester",
            )
    finally:
        http_client.close()

    assert str(exc_info.value) == "Failed to trigger workflow run"


def test_http_executor_validate_credentials_uses_fallback_transport() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/workflows/workflow-1/credentials/validate"
        assert json.loads(request.content) == {"actor": "tester"}
        assert request.headers["authorization"] == "Bearer secret"
        assert request.headers["x-request"] == "1"
        return httpx.Response(200, json={"validated": True})

    transport = httpx.MockTransport(handler)
    executor = HttpWorkflowExecutor(
        client=OrcheoClient(base_url="http://localhost"),
        auth_token="secret",
        transport=transport,
        max_retries=0,
    )

    result = executor.validate_credentials(
        " workflow-1 ",
        actor="tester",
        headers={"X-Request": "1"},
    )

    assert len(requests) == 1
    assert result == {"validated": True}


def test_http_executor_validate_credentials_rejects_empty_workflow_id() -> None:
    executor = HttpWorkflowExecutor(client=OrcheoClient(base_url="http://localhost"))

    with pytest.raises(ValueError):
        executor.validate_credentials("   ")


def test_http_executor_get_uses_fallback_transport_and_absolute_urls() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    executor = HttpWorkflowExecutor(
        client=OrcheoClient(base_url="http://localhost"),
        transport=transport,
    )

    response = executor._get("http://localhost/api/ping", {"X-Test": "1"})

    assert response.json() == {"ok": True}
    assert len(requests) == 1
    assert requests[0].url.path == "/api/ping"
    assert (
        executor._relative_url(
            "https://example.com/ping",
            "http://localhost",
        )
        == "https://example.com/ping"
    )


def test_http_executor_get_uses_injected_http_client() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://localhost",
    )
    executor = HttpWorkflowExecutor(
        client=OrcheoClient(base_url="http://localhost"),
        http_client=http_client,
    )

    try:
        response = executor._get("http://localhost/api/ping", {"X-Test": "1"})
    finally:
        http_client.close()

    assert response.json() == {"ok": True}
    assert len(requests) == 1
    assert requests[0].url.path == "/api/ping"


def test_http_executor_get_uses_httpx_client_constructor_without_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object,
        ) -> bool:
            return False

        def get(
            self,
            url: str,
            headers: Mapping[str, str],
        ) -> httpx.Response:
            captured["url"] = url
            captured["headers"] = dict(headers)
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "Client", FakeClient)

    executor = HttpWorkflowExecutor(
        client=OrcheoClient(base_url="http://localhost"),
    )
    response = executor._get("https://example.com/ping", {"X-Test": "1"})

    assert response.json() == {"ok": True}
    assert captured["kwargs"] == {"timeout": 30.0, "base_url": "http://localhost"}
    assert captured["url"] == "https://example.com/ping"
    assert captured["headers"] == {"X-Test": "1"}


def test_http_executor_validate_credentials_uses_httpx_client_constructor_without_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object,
        ) -> bool:
            return False

        def post(
            self,
            url: str,
            json: Mapping[str, object],
            headers: Mapping[str, str],
        ) -> httpx.Response:
            captured["url"] = url
            captured["json"] = dict(json)
            captured["headers"] = dict(headers)
            request = httpx.Request("POST", f"http://localhost{url}")
            return httpx.Response(200, request=request, json={"validated": True})

    monkeypatch.setattr(httpx, "Client", FakeClient)

    executor = HttpWorkflowExecutor(
        client=OrcheoClient(base_url="http://localhost"),
        auth_token="secret",
    )
    response = executor.validate_credentials(
        "workflow-1",
        actor="tester",
        headers={"X-Request": "1"},
    )

    assert response == {"validated": True}
    assert captured["kwargs"] == {"timeout": 30.0, "base_url": "http://localhost"}
    assert captured["url"] == "/api/workflows/workflow-1/credentials/validate"
    assert captured["json"] == {"actor": "tester"}
    assert captured["headers"] == {
        "X-Request": "1",
        "Authorization": "Bearer secret",
    }
