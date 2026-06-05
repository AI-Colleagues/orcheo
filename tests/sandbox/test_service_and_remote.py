"""Tests for the sandbox-runtime HTTP service and its remote clients."""

from __future__ import annotations
import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
import socket
import httpx
import pytest
from fastapi.testclient import TestClient
from orcheo.sandbox import service as service_module
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.sandbox.errors import SandboxAcquireError
from orcheo.sandbox.manager import SandboxRuntimeManager
from orcheo.sandbox.remote import (
    RemoteContainerRuntime,
    RemoteRuntimeError,
    RemoteSandboxExec,
    RemoteSandboxIngestor,
    RemoteSandboxManager,
    RemoteSandboxRunner,
    _control_headers,
)
from orcheo.sandbox.config import SandboxSettings
from orcheo.sandbox.models import SandboxState
from orcheo.sandbox.runtime import ContainerSpec, InMemoryContainerRuntime
from orcheo.sandbox.service import (
    ContainerExecutor,
    ExecRequest,
    ScriptIngestionPayload,
    ScriptSandboxInvoker,
    SandboxProvisionRequest,
    WorkflowSandboxInvoker,
    build_credential_relay_app,
    build_service_app,
    _control_dependency,
    _resolve_extra_hosts,
    _sandbox_environment,
)
from orcheo.sandbox.workflow import WorkflowRunResult, WorkflowRunSpec


_CONTROL_HEADERS = {"X-Orcheo-Sandbox-Control-Token": "control-test-token"}


class _FakeExecutor(ContainerExecutor):
    """Test double that records exec calls and returns a canned result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ExecRequest]] = []
        self.result = ProcessExecutionResult(
            command=["echo", "ok"],
            stdout="ok",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.01,
        )

    async def exec(
        self, sandbox_id: str, request: ExecRequest
    ) -> ProcessExecutionResult:
        self.calls.append((sandbox_id, request))
        return self.result


class _FakeInvoker(WorkflowSandboxInvoker):
    """Test double that records invocations and returns a canned result."""

    def __init__(self) -> None:
        # Skip parent __init__ (no executor needed).
        self.calls: list[tuple[str, WorkflowRunSpec, str]] = []
        self.result = WorkflowRunResult(
            run_id="r1",
            status="succeeded",
            outputs={"hello": "world"},
            error=None,
        )

    async def invoke(
        self,
        sandbox_id: str,
        spec: WorkflowRunSpec,
        broker_token: str,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkflowRunResult:
        del timeout_seconds
        self.calls.append((sandbox_id, spec, broker_token))
        return self.result


@pytest.fixture
def app_with_fakes() -> tuple[
    TestClient, InMemoryContainerRuntime, _FakeExecutor, _FakeInvoker
]:
    """Build the FastAPI app with in-memory runtime and fake executor / invoker."""
    runtime = InMemoryContainerRuntime()
    executor = _FakeExecutor()
    invoker = _FakeInvoker()
    app = build_service_app(
        runtime=runtime,
        executor=executor,
        invoker=invoker,
        settings=SandboxSettings(
            credential_broker_url="http://10.99.0.2:9091/credentials/resolve"
        ),
        control_token="control-test-token",
    )
    return TestClient(app, headers=_CONTROL_HEADERS), runtime, executor, invoker


def test_healthz(app_with_fakes: tuple[TestClient, Any, Any, Any]) -> None:
    """The service exposes a /healthz endpoint."""
    client, *_ = app_with_fakes
    assert client.get("/healthz").json() == {"status": "ok"}


def test_control_routes_require_valid_token(
    app_with_fakes: tuple[TestClient, Any, Any, Any],
) -> None:
    """The Docker-facing API fails closed without its internal credential."""
    authenticated, *_ = app_with_fakes
    client = TestClient(authenticated.app)
    payload = {"workspace_id": "ws"}
    assert client.post("/internal/containers", json=payload).status_code == 401
    assert (
        client.post(
            "/internal/containers",
            json=payload,
            headers={"X-Orcheo-Sandbox-Control-Token": "wrong"},
        ).status_code
        == 401
    )


def test_provisioning_rejects_isolation_override_fields(
    app_with_fakes: tuple[TestClient, Any, Any, Any],
) -> None:
    """Callers cannot choose image, runtime, or privilege configuration."""
    client, *_ = app_with_fakes
    response = client.post(
        "/internal/containers",
        json={"workspace_id": "ws", "image": "attacker/image", "runtime": "runc"},
    )
    assert response.status_code == 422


def test_relay_has_no_lifecycle_or_exec_routes() -> None:
    """The child-reachable relay cannot reach container control operations."""
    client = TestClient(build_credential_relay_app())
    assert (
        client.post("/internal/containers", json={"workspace_id": "ws"}).status_code
        == 404
    )
    assert (
        client.post("/internal/sandboxes/sb/exec", json={"command": []}).status_code
        == 404
    )
    assert client.get("/healthz").json() == {"status": "ok"}


def test_sandbox_provision_request_includes_proxy_environment_and_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provisioning inherits the fixed sandbox environment and host pinning."""

    settings = SandboxSettings(
        credential_broker_url="http://relay.local:9091/credentials/resolve",
        egress_proxy_url="http://proxy.local:3128",
    )

    monkeypatch.setattr(
        socket,
        "gethostbyname",
        lambda host: {"relay.local": "10.0.0.2", "proxy.local": "10.0.0.3"}[host],
    )

    spec = SandboxProvisionRequest(workspace_id="ws").to_spec(settings)

    assert spec.environment["ORCHEO_CREDENTIAL_BROKER_URL"] == (
        "http://relay.local:9091/credentials/resolve"
    )
    assert spec.environment["HTTP_PROXY"] == "http://proxy.local:3128"
    assert spec.environment["NO_PROXY"] == "localhost,127.0.0.1,relay.local"
    assert spec.extra_hosts == {
        "relay.local": "10.0.0.2",
        "proxy.local": "10.0.0.3",
    }


def test_sandbox_environment_omits_missing_broker_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO_PROXY stays limited to localhost when the broker URL lacks a hostname."""

    settings = SandboxSettings(
        credential_broker_url="http:///credentials/resolve",
        egress_proxy_url="http://proxy.local:3128",
    )

    monkeypatch.setattr(
        socket,
        "gethostbyname",
        lambda host: {"proxy.local": "10.0.0.3"}[host],
    )

    environment = _sandbox_environment(settings)

    assert environment["NO_PROXY"] == "localhost,127.0.0.1"


def test_resolve_extra_hosts_skips_literal_ip_addresses() -> None:
    """IP-literal service URLs do not need hostfile pinning."""

    settings = SandboxSettings(
        credential_broker_url="http://10.0.0.2:9091/credentials/resolve",
        egress_proxy_url="http://[::1]:3128",
    )

    assert _resolve_extra_hosts(settings) == {}


def test_resolve_extra_hosts_skips_duplicate_hostnames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname pinned once is not resolved again for the egress proxy."""

    settings = SandboxSettings(
        credential_broker_url="http://relay.local:9091/credentials/resolve",
        egress_proxy_url="http://relay.local:3128",
    )
    calls: list[str] = []

    def fake_gethostbyname(host: str) -> str:
        calls.append(host)
        return "10.0.0.2"

    monkeypatch.setattr(socket, "gethostbyname", fake_gethostbyname)

    hosts = _resolve_extra_hosts(settings)

    assert hosts == {"relay.local": "10.0.0.2"}
    assert calls == ["relay.local"]


def test_resolve_extra_hosts_raises_when_host_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing hostname fails fast with a 503."""

    settings = SandboxSettings(
        credential_broker_url="http://relay.local:9091/credentials/resolve"
    )
    monkeypatch.setattr(
        socket, "gethostbyname", lambda host: (_ for _ in ()).throw(OSError(host))
    )

    with pytest.raises(Exception) as exc_info:
        _resolve_extra_hosts(settings)

    assert getattr(exc_info.value, "status_code", None) == 503


def test_control_dependency_requires_token() -> None:
    """The control-plane dependency refuses to start without a shared secret."""
    with pytest.raises(RuntimeError, match="must be configured"):
        _control_dependency("")


def test_control_headers_require_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime clients fail closed when no control token is available."""
    monkeypatch.delenv("ORCHEO_SANDBOX_CONTROL_TOKEN", raising=False)
    with pytest.raises(RemoteRuntimeError, match="required for runtime requests"):
        _control_headers(None)


def test_ingestion_invoker_never_injects_broker_token() -> None:
    """Script validation executes with no credential token environment."""
    executor = _FakeExecutor()
    executor.result = ProcessExecutionResult(
        command=[],
        stdout='{"status":"succeeded","payload":{"format":"langgraph-script"}}',
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0.01,
    )
    payload = ScriptIngestionPayload(source="source")
    result = asyncio.run(ScriptSandboxInvoker(executor).invoke("sandbox", payload))
    assert result == {"format": "langgraph-script"}
    assert executor.calls[0][1].command == [
        "python",
        "-m",
        "orcheo.sandbox.ingestion_runner",
    ]
    assert executor.calls[0][1].env is None
    assert json.loads(executor.calls[0][1].stdin or "{}") == {
        "source": "source",
        "entrypoint": None,
        "max_script_bytes": 524288,
        "execution_timeout_seconds": 60.0,
    }


def test_ingestion_invoker_reports_missing_json_output() -> None:
    """Malformed runner output becomes a 400 with a stable error message."""
    executor = _FakeExecutor()
    executor.result = ProcessExecutionResult(
        command=[],
        stdout="not json",
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0.01,
    )
    with pytest.raises(Exception, match="no output"):
        asyncio.run(
            ScriptSandboxInvoker(executor).invoke(
                "sandbox", ScriptIngestionPayload(source="graph = object()")
            )
        )


def test_ingestion_invoker_reports_failed_status() -> None:
    """A failed ingestion status is surfaced from the runner payload."""
    executor = _FakeExecutor()
    executor.result = ProcessExecutionResult(
        command=[],
        stdout='{"status":"failed","error":"bad script"}',
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0.01,
    )
    with pytest.raises(Exception, match="bad script"):
        asyncio.run(
            ScriptSandboxInvoker(executor).invoke(
                "sandbox", ScriptIngestionPayload(source="graph = object()")
            )
        )


def test_ingestion_invoker_reports_timeout() -> None:
    """One-shot script validation fails when the sandbox process times out."""
    executor = _FakeExecutor()
    executor.result = ProcessExecutionResult(
        command=[],
        stdout="",
        stderr="",
        exit_code=None,
        timed_out=True,
        duration_seconds=1.0,
    )
    with pytest.raises(Exception, match="timed out"):
        asyncio.run(
            ScriptSandboxInvoker(executor).invoke(
                "sandbox", ScriptIngestionPayload(source="while True: pass")
            )
        )


def test_ingestion_endpoint_rejects_disabled_limits(
    app_with_fakes: tuple[TestClient, Any, Any, Any],
) -> None:
    """Authenticated callers cannot turn off tenant ingestion bounds."""
    client, *_ = app_with_fakes
    response = client.post(
        "/internal/sandboxes/sandbox/ingest",
        json={
            "source": "graph = object()",
            "max_script_bytes": None,
            "execution_timeout_seconds": None,
        },
    )
    assert response.status_code == 422


def test_ingestion_endpoint_calls_invoker(
    app_with_fakes: tuple[TestClient, Any, _FakeExecutor, Any],
) -> None:
    """A valid payload reaches the sandbox ingestion path."""
    client, _runtime, executor, _invoker = app_with_fakes
    executor.result = ProcessExecutionResult(
        command=[],
        stdout='{"status":"succeeded","payload":{"graph":"ok"}}',
        stderr="",
        exit_code=0,
        timed_out=False,
        duration_seconds=0.01,
    )
    response = client.post(
        "/internal/sandboxes/sandbox/ingest",
        json={"source": "graph = object()"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"graph": "ok"}


def test_credential_relay_forwards_broker_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sandbox credential requests cross only the relay application."""
    client = TestClient(build_credential_relay_app())
    calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 30.0

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> httpx.Response:
            calls.append((url, json, headers))
            return httpx.Response(200, json={"value": "resolved"})

    monkeypatch.setenv(
        "ORCHEO_CREDENTIAL_BROKER_FORWARD_URL",
        "http://backend/internal/credentials/resolve",
    )
    monkeypatch.setattr(service_module.httpx, "AsyncClient", _FakeAsyncClient)

    response = client.post(
        "/credentials/resolve",
        json={"run_id": "run-1", "credential_name": "openai_api_key"},
        headers={
            "Authorization": "Bearer broker-token",
            "X-Orcheo-Workspace": "ws-1",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"value": "resolved"}
    assert calls == [
        (
            "http://backend/internal/credentials/resolve",
            {"run_id": "run-1", "credential_name": "openai_api_key"},
            {
                "Authorization": "Bearer broker-token",
                "X-Orcheo-Workspace": "ws-1",
            },
        )
    ]


def test_credential_relay_proxies_chatkit_attachment_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sandbox attachment downloads are proxied through the child-facing relay."""
    client = TestClient(build_credential_relay_app())
    calls: list[str] = []

    class _AttachmentAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 30.0

        async def __aenter__(self) -> _AttachmentAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            calls.append(url)
            return httpx.Response(
                200,
                content=b"alpha,beta\n1,2\n",
                headers={
                    "content-type": "text/csv",
                    "content-disposition": 'attachment; filename="report.csv"',
                },
            )

    monkeypatch.setenv(
        "ORCHEO_CHATKIT_ATTACHMENT_FORWARD_URL",
        "http://backend:2025/api/chatkit/attachments",
    )
    monkeypatch.setattr(service_module.httpx, "AsyncClient", _AttachmentAsyncClient)

    response = client.get("/api/chatkit/attachments/atc_123")

    assert response.status_code == 200
    assert response.content == b"alpha,beta\n1,2\n"
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="report.csv"'
    )
    assert calls == ["http://backend:2025/api/chatkit/attachments/atc_123"]


def test_credential_relay_proxies_chatkit_attachment_without_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content-Disposition header absent → branch 595->597 (False path) is taken."""
    client = TestClient(build_credential_relay_app())

    class _NoDispositionClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _NoDispositionClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"raw-bytes",
                headers={"content-type": "application/octet-stream"},
                # No content-disposition header
            )

    monkeypatch.setattr(service_module.httpx, "AsyncClient", _NoDispositionClient)

    response = client.get("/api/chatkit/attachments/atc_no_cd")

    assert response.status_code == 200
    assert response.content == b"raw-bytes"
    # No Content-Disposition header should be present in relay response
    assert "content-disposition" not in response.headers


def test_credential_relay_proxies_chatkit_attachment_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attachment upload is proxied through the relay (lines 608-630)."""
    client = TestClient(build_credential_relay_app())
    upload_calls: list[dict] = []

    class _UploadClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 60.0

        async def __aenter__(self) -> _UploadClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            content: bytes,
            headers: dict,
        ) -> httpx.Response:
            upload_calls.append({"url": url, "headers": headers, "content": content})
            return httpx.Response(
                200,
                json={
                    "id": "atc_uploaded",
                    "download_url": "http://backend/api/chatkit/attachments/atc_uploaded",
                },
                headers={"content-type": "application/json"},
            )

    monkeypatch.setenv(
        "ORCHEO_CHATKIT_ATTACHMENT_UPLOAD_FORWARD_URL",
        "http://backend:2025/internal/attachments/upload",
    )
    monkeypatch.setattr(service_module.httpx, "AsyncClient", _UploadClient)

    response = client.post(
        "/api/chatkit/attachments/upload",
        content=b"multipart-body",
        headers={
            "Authorization": "Bearer sandbox-token",
            "Content-Type": "multipart/form-data; boundary=abc",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "atc_uploaded"
    assert len(upload_calls) == 1
    assert upload_calls[0]["url"] == "http://backend:2025/internal/attachments/upload"
    assert upload_calls[0]["headers"]["Authorization"] == "Bearer sandbox-token"
    assert upload_calls[0]["content"] == b"multipart-body"


def test_credential_relay_proxies_chatkit_attachment_upload_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload without Authorization/Content-Type headers exercises lines 619->621, 622->624."""
    client = TestClient(build_credential_relay_app())
    upload_calls: list[dict] = []

    class _UploadNoAuthClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _UploadNoAuthClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            content: bytes,
            headers: dict,
        ) -> httpx.Response:
            upload_calls.append(
                {"url": url, "headers": dict(headers), "content": content}
            )
            return httpx.Response(
                200,
                json={"id": "atc_noauth"},
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(service_module.httpx, "AsyncClient", _UploadNoAuthClient)

    # No Authorization, no Content-Type headers
    response = client.post(
        "/api/chatkit/attachments/upload",
        content=b"raw-body",
    )

    assert response.status_code == 200
    assert len(upload_calls) == 1
    # No auth or content-type should be forwarded
    assert "Authorization" not in upload_calls[0]["headers"]


def test_provision_stop_lifecycle(
    app_with_fakes: tuple[TestClient, InMemoryContainerRuntime, Any, Any],
) -> None:
    """POST /containers + DELETE round-trips through the underlying runtime."""
    client, runtime, _executor, _invoker = app_with_fakes
    spec = ContainerSpec(
        image="img",
        workspace_id="ws",
        runtime="runsc",
        command=("agent",),
        environment={"K": "V"},
        dns=("1.1.1.1",),
        extra_hosts={"sandbox-runtime": "10.0.0.7"},
    )
    response = client.post("/internal/containers", json=_spec_payload(spec))
    assert response.status_code == 201, response.text
    container_id = response.json()["container_id"]
    assert runtime.started, "runtime.start was not invoked"
    _, started_spec = runtime.started[0]
    assert started_spec.dns == ()
    assert started_spec.extra_hosts == {}
    assert started_spec.image != spec.image
    assert started_spec.network_mode == "sandbox-egress"
    assert started_spec.cap_drop == ("ALL",)

    inspect = client.get(f"/internal/containers/{container_id}")
    assert inspect.status_code == 200
    assert inspect.json()["running"] is True

    stop = client.delete(f"/internal/containers/{container_id}")
    assert stop.status_code == 204
    assert runtime.stopped, "runtime.stop was not invoked"

    # Stopping again is a 404 because the container is no longer running.
    assert client.delete(f"/internal/containers/{container_id}").status_code == 404


def test_stop_works_without_cached_handle(
    app_with_fakes: tuple[
        TestClient, InMemoryContainerRuntime, _FakeExecutor, _FakeInvoker
    ],
) -> None:
    """DELETE /containers/{id} still stops a running container after restart."""
    client, runtime, executor, invoker = app_with_fakes
    spec = ContainerSpec(image="img", workspace_id="ws")
    provision = client.post("/internal/containers", json=_spec_payload(spec))
    container_id = provision.json()["container_id"]

    # Simulate runtime-service restart by rebuilding the app with the same runtime.
    running_handle, _ = runtime.started[0]
    assert runtime.is_running(running_handle) is True
    restarted_client = TestClient(
        build_service_app(
            runtime=runtime,
            executor=executor,
            invoker=invoker,
            settings=SandboxSettings(
                credential_broker_url="http://10.99.0.2:9091/credentials/resolve"
            ),
            control_token="control-test-token",
        ),
        headers=_CONTROL_HEADERS,
    )

    stop = restarted_client.delete(f"/internal/containers/{container_id}")
    assert stop.status_code == 204
    assert runtime.is_running(running_handle) is False


def test_exec_endpoint_calls_executor(
    app_with_fakes: tuple[TestClient, Any, _FakeExecutor, Any],
) -> None:
    """POST /sandboxes/{id}/exec routes through ContainerExecutor.exec."""
    client, _runtime, executor, _invoker = app_with_fakes
    response = client.post(
        "/internal/sandboxes/sb-1/exec",
        json={
            "command": ["echo", "hello"],
            "cwd": "/scratch",
            "env": {"FOO": "bar"},
            "timeout_seconds": 5.0,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["exit_code"] == 0
    assert executor.calls == [
        (
            "sb-1",
            ExecRequest(
                command=["echo", "hello"],
                cwd="/scratch",
                env={"FOO": "bar"},
                timeout_seconds=5.0,
            ),
        )
    ]


def test_dispatch_workflow_endpoint(
    app_with_fakes: tuple[TestClient, Any, Any, _FakeInvoker],
) -> None:
    """POST /sandboxes/{id}/dispatch_workflow goes through the invoker."""
    client, _runtime, _executor, invoker = app_with_fakes
    payload = {
        "spec": {
            "run_id": "r-1",
            "workspace_id": "ws",
            "workflow_definition": {"nodes": []},
            "inputs": {"a": 1},
            "node_types": ["AINode"],
            "runnable_config": {"configurable": {"thread_id": "r-1"}},
            "state_config": {"configurable": {"ai_model": "openai:test"}},
        },
        "broker_token": "tok-1",
    }
    response = client.post("/internal/sandboxes/sb-1/dispatch_workflow", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "succeeded"
    assert data["outputs"] == {"hello": "world"}
    sandbox_id, spec, broker_token = invoker.calls[0]
    assert sandbox_id == "sb-1"
    assert spec.workspace_id == "ws"
    assert spec.node_types == ("AINode",)
    assert spec.runnable_config == {"configurable": {"thread_id": "r-1"}}
    assert spec.state_config == {"configurable": {"ai_model": "openai:test"}}
    assert broker_token == "tok-1"


def test_workflow_invoker_uses_stdin_for_payload() -> None:
    """Workflow dispatch must stream JSON over stdin instead of argv."""
    import unittest.mock as mock

    async def go() -> WorkflowRunResult:
        from orcheo.sandbox.service import WorkflowSandboxInvoker
        from orcheo.sandbox.workflow import WorkflowRunSpec

        executor = mock.AsyncMock()
        executor.exec = mock.AsyncMock(
            return_value=ProcessExecutionResult(
                command=["python", "-m", "orcheo.sandbox.workflow_runner"],
                stdout='{"status":"succeeded","outputs":{"ok":true},"error":null}\n',
                stderr="",
                exit_code=0,
                timed_out=False,
                duration_seconds=0.1,
            )
        )
        invoker = WorkflowSandboxInvoker(executor)
        spec = WorkflowRunSpec(
            run_id="r-ok",
            workspace_id="ws",
            workflow_definition={"nodes": [{"type": "AINode"}]},
            inputs={"value": 42},
            node_types=("AINode",),
            runnable_config={"configurable": {"thread_id": "r-ok"}},
            state_config={"configurable": {"ai_model": "openai:test"}},
        )

        result = await invoker.invoke("sb-1", spec, "token")

        call = executor.exec.await_args
        assert call.args[0] == "sb-1"
        request = call.args[1]
        assert request.command == ["python", "-m", "orcheo.sandbox.workflow_runner"]
        assert request.stdin is not None
        payload = json.loads(request.stdin)
        assert payload["run_id"] == "r-ok"
        assert payload["workspace_id"] == "ws"
        assert payload["workflow_definition"] == {"nodes": [{"type": "AINode"}]}
        assert payload["inputs"] == {"value": 42}
        assert payload["runnable_config"] == {"configurable": {"thread_id": "r-ok"}}
        assert payload["state_config"] == {"configurable": {"ai_model": "openai:test"}}
        assert request.env == {"ORCHEO_BROKER_TOKEN": "token"}
        return result

    result = asyncio.run(go())
    assert result.status == "succeeded"
    assert result.outputs == {"ok": True}


def test_remote_container_runtime_round_trip() -> None:
    """RemoteContainerRuntime parses runtime-service responses correctly."""
    state: dict[str, Any] = {"running": True}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-orcheo-sandbox-control-token"] == "control-test-token"
        if request.method == "POST" and request.url.path == "/internal/containers":
            payload = request.read().decode("utf-8")
            assert '"workspace_id":"ws-remote"' in payload
            assert '"dns"' not in payload
            assert '"image"' not in payload
            return httpx.Response(
                201,
                json={
                    "container_id": "c-1",
                    "image": "img",
                    "workspace_id": "ws-remote",
                    "runtime": "runsc",
                },
            )
        if request.method == "GET" and request.url.path == "/internal/containers/c-1":
            return httpx.Response(
                200,
                json={
                    "container_id": "c-1",
                    "image": "img",
                    "workspace_id": "ws-remote",
                    "runtime": "runsc",
                    "running": state["running"],
                },
            )
        if (
            request.method == "DELETE"
            and request.url.path == "/internal/containers/c-1"
        ):
            state["running"] = False
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    sync_client = httpx.Client(transport=transport, base_url="http://test")
    remote = RemoteContainerRuntime(
        "http://test", control_token="control-test-token", client=sync_client
    )
    try:
        handle = remote.start(
            ContainerSpec(
                image="img",
                workspace_id="ws-remote",
                dns=("1.1.1.1", "8.8.8.8"),
                extra_hosts={"sandbox-runtime": "10.0.0.7"},
            )
        )
        assert handle.workspace_id == "ws-remote"
        assert remote.is_running(handle) is True
        remote.stop(handle)
        assert state["running"] is False
    finally:
        remote.close()


def test_remote_ingestor_aclose_closes_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ingestion client closes its owned HTTP client."""

    closed = {"value": False}

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 60.0

        async def aclose(self) -> None:
            closed["value"] = True

    monkeypatch.setattr("orcheo.sandbox.remote.httpx.AsyncClient", _FakeAsyncClient)

    async def go() -> None:
        ingestor = RemoteSandboxIngestor(
            "http://test", control_token="control-test-token"
        )
        await ingestor.aclose()

    asyncio.run(go())
    assert closed["value"] is True


def test_remote_ingestor_aclose_does_not_close_borrowed_client() -> None:
    """A borrowed client is left alone when the wrapper is closed."""

    closed = {"value": False}

    class _FakeAsyncClient:
        async def aclose(self) -> None:
            closed["value"] = True

    ingestor = RemoteSandboxIngestor(
        "http://test",
        control_token="control-test-token",
        client=_FakeAsyncClient(),
    )

    async def go() -> None:
        await ingestor.aclose()

    asyncio.run(go())
    assert closed["value"] is False


def test_remote_ingestor_returns_payload() -> None:
    """RemoteSandboxIngestor parses the service response on success."""

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 60.0

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
            timeout: float,
        ) -> httpx.Response:
            assert url.endswith("/internal/sandboxes/sb-1/ingest")
            assert headers["X-Orcheo-Sandbox-Control-Token"] == "control-test-token"
            assert json["max_script_bytes"] > 0
            assert timeout > 30.0
            return httpx.Response(200, json={"payload": {"graph": "ok"}})

    ingestor = RemoteSandboxIngestor(
        "http://test",
        control_token="control-test-token",
        client=_FakeAsyncClient(timeout=60.0),
    )

    async def go() -> dict[str, Any]:
        return await ingestor.ingest(
            "sb-1",
            source="graph = object()",
            entrypoint=None,
            max_script_bytes=10,
            execution_timeout_seconds=5.0,
        )

    assert asyncio.run(go()) == {"payload": {"graph": "ok"}}


def test_remote_ingestor_raises_script_ingestion_error() -> None:
    """A 400 response is mapped to ScriptIngestionError."""

    class _FakeAsyncClient:
        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
            timeout: float,
        ) -> httpx.Response:
            del url, headers, json, timeout
            return httpx.Response(400, json={"detail": "bad script"})

    ingestor = RemoteSandboxIngestor(
        "http://test", control_token="control-test-token", client=_FakeAsyncClient()
    )

    async def go() -> None:
        with pytest.raises(Exception, match="bad script"):
            await ingestor.ingest(
                "sb-1",
                source="graph = object()",
                entrypoint=None,
                max_script_bytes=10,
                execution_timeout_seconds=5.0,
            )

    asyncio.run(go())


def test_remote_ingestor_raises_runtime_error_on_non_bad_request() -> None:
    """Non-400 failures bubble up as RemoteRuntimeError."""

    class _FakeAsyncClient:
        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
            timeout: float,
        ) -> httpx.Response:
            del url, headers, json, timeout
            return httpx.Response(500, json={"detail": "boom"})

    ingestor = RemoteSandboxIngestor(
        "http://test", control_token="control-test-token", client=_FakeAsyncClient()
    )

    async def go() -> None:
        with pytest.raises(RemoteRuntimeError, match="ingestion failed"):
            await ingestor.ingest(
                "sb-1",
                source="graph = object()",
                entrypoint=None,
                max_script_bytes=10,
                execution_timeout_seconds=5.0,
            )

    asyncio.run(go())


def test_remote_ingestor_handles_non_json_error_body() -> None:
    """Non-JSON responses still surface a readable failure."""

    class _FakeAsyncClient:
        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
            timeout: float,
        ) -> httpx.Response:
            del url, headers, json, timeout
            return httpx.Response(500, text="plain text error")

    ingestor = RemoteSandboxIngestor(
        "http://test", control_token="control-test-token", client=_FakeAsyncClient()
    )

    async def go() -> None:
        with pytest.raises(RemoteRuntimeError, match="plain text error"):
            await ingestor.ingest(
                "sb-1",
                source="graph = object()",
                entrypoint=None,
                max_script_bytes=10,
                execution_timeout_seconds=5.0,
            )

    asyncio.run(go())


def test_remote_exec_returns_process_result(
    app_with_fakes: tuple[TestClient, Any, _FakeExecutor, Any],
) -> None:
    """RemoteSandboxExec parses the service's JSON into ProcessExecutionResult."""
    client, _runtime, executor, _invoker = app_with_fakes
    transport = httpx.ASGITransport(app=client.app)
    async_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    backend = RemoteSandboxExec(
        "http://testserver", control_token="control-test-token", client=async_client
    )

    async def go() -> ProcessExecutionResult:
        try:
            return await backend.exec(
                "sb-1",
                ["echo", "hi"],
                cwd=Path("/scratch"),
                env={"FOO": "bar"},
                timeout_seconds=2.5,
            )
        finally:
            await async_client.aclose()

    result = asyncio.run(go())
    assert result.exit_code == 0
    assert executor.calls[0][0] == "sb-1"


def test_remote_runner_dispatches_workflow(
    app_with_fakes: tuple[TestClient, Any, Any, _FakeInvoker],
) -> None:
    """RemoteSandboxRunner returns a WorkflowRunResult parsed from the service."""
    client, _runtime, _executor, invoker = app_with_fakes
    transport = httpx.ASGITransport(app=client.app)
    async_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    runner = RemoteSandboxRunner(
        "http://testserver", control_token="control-test-token", client=async_client
    )

    spec = WorkflowRunSpec(
        run_id="r-2",
        workspace_id="ws-2",
        workflow_definition={"nodes": [{"type": "AINode"}]},
        inputs={},
        node_types=("AINode",),
        runnable_config={"configurable": {"thread_id": "r-2"}},
        state_config={"configurable": {"ai_model": "openai:test"}},
    )

    async def go() -> WorkflowRunResult:
        try:
            from orcheo.sandbox.models import SandboxLease, SandboxState

            lease = SandboxLease(
                lease_id="l",
                workspace_id="ws-2",
                sandbox_id="sb-9",
                state=SandboxState.IN_USE,
            )
            return await runner.execute(lease, spec, "broker-token")
        finally:
            await async_client.aclose()

    result = asyncio.run(go())
    assert result.status == "succeeded"
    assert result.outputs == {"hello": "world"}
    assert invoker.calls[0][1].runnable_config == {"configurable": {"thread_id": "r-2"}}
    assert invoker.calls[0][1].state_config == {
        "configurable": {"ai_model": "openai:test"}
    }
    assert invoker.calls[0][2] == "broker-token"


def _spec_payload(spec: ContainerSpec) -> Mapping[str, Any]:
    """Render only constrained fields accepted by provisioning."""
    return {
        "workspace_id": spec.workspace_id,
        "cpu_limit": spec.cpu_limit,
        "memory_limit": spec.memory_limit,
        "pid_limit": spec.pid_limit,
        "scratch_size": spec.scratch_size,
    }


# ---------------------------------------------------------------------------
# RemoteContainerRuntime additional coverage (lines 96, 113, 122, 131-138)
# ---------------------------------------------------------------------------


def test_remote_container_runtime_close_owned_client() -> None:
    """close() disposes of the client when the runtime owns it (line 96)."""
    closed: list[bool] = []

    class _TrackingClient:
        def close(self) -> None:
            closed.append(True)

    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    real_client = httpx.Client(transport=transport)
    runtime = RemoteContainerRuntime("http://test", client=real_client)
    # Override the client with the tracking one for the close test.
    runtime._client = _TrackingClient()  # type: ignore[assignment]
    runtime._owns_client = True
    runtime.close()
    assert closed == [True]


def test_remote_container_runtime_close_unowned_client_does_nothing() -> None:
    """close() skips disposal when the runtime does NOT own the client."""
    closed: list[bool] = []

    class _TrackingClient:
        def close(self) -> None:
            closed.append(True)

    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    real_client = httpx.Client(transport=transport)
    runtime = RemoteContainerRuntime("http://test", client=real_client)
    runtime._client = _TrackingClient()  # type: ignore[assignment]
    runtime._owns_client = False
    runtime.close()
    assert closed == []


def test_remote_container_runtime_stop_returns_on_404() -> None:
    """stop() returns early without raising when the container is already gone (line 113)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test")
    runtime = RemoteContainerRuntime("http://test", client=client)
    from orcheo.sandbox.runtime import ContainerHandle

    handle = ContainerHandle(
        container_id="gone-c1", image="img", workspace_id="ws", runtime="runsc"
    )
    runtime.stop(handle)  # must not raise


def test_remote_container_runtime_is_running_returns_false_on_404() -> None:
    """is_running() returns False when the service reports 404 (line 122)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test")
    runtime = RemoteContainerRuntime("http://test", client=client)
    from orcheo.sandbox.runtime import ContainerHandle

    handle = ContainerHandle(
        container_id="no-c1", image="img", workspace_id="ws", runtime="runsc"
    )
    assert runtime.is_running(handle) is False


def test_remote_container_runtime_raise_for_status_conflict_raises_acquire_error() -> (
    None
):
    """409 Conflict translates to SandboxAcquireError (lines 131-138)."""
    from orcheo.sandbox.errors import SandboxAcquireError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "conflict"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test")
    runtime = RemoteContainerRuntime("http://test", client=client)
    spec = ContainerSpec(image="img", workspace_id="ws")
    with pytest.raises(SandboxAcquireError):
        runtime.start(spec)


def test_remote_container_runtime_raise_for_status_other_error() -> None:
    """Non-2xx non-409 responses raise RemoteRuntimeError (lines 131-138)."""
    from orcheo.sandbox.remote import RemoteRuntimeError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "server error"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test")
    runtime = RemoteContainerRuntime("http://test", client=client)
    spec = ContainerSpec(image="img", workspace_id="ws")
    with pytest.raises(RemoteRuntimeError, match="500"):
        runtime.start(spec)


def test_remote_container_runtime_raise_for_status_non_json_body() -> None:
    """Non-JSON response body falls back to raw text in the error (lines 131-138)."""
    from orcheo.sandbox.remote import RemoteRuntimeError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, content=b"Service Unavailable", headers={"content-type": "text/plain"}
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test")
    runtime = RemoteContainerRuntime("http://test", client=client)
    spec = ContainerSpec(image="img", workspace_id="ws")
    with pytest.raises(RemoteRuntimeError):
        runtime.start(spec)


# ---------------------------------------------------------------------------
# RemoteSandboxExec additional coverage (lines 165-166, 199-204)
# ---------------------------------------------------------------------------


def test_remote_sandbox_exec_aclose_owned_client() -> None:
    """aclose() closes the underlying client when owned (lines 165-166)."""
    closed: list[bool] = []

    class _TrackingAsyncClient:
        async def aclose(self) -> None:
            closed.append(True)

    backend = RemoteSandboxExec("http://test")
    backend._client = _TrackingAsyncClient()  # type: ignore[assignment]
    backend._owns_client = True
    asyncio.run(backend.aclose())
    assert closed == [True]


def test_remote_sandbox_exec_aclose_unowned_client_skips() -> None:
    """aclose() skips disposal when not owning the client."""
    closed: list[bool] = []

    class _TrackingAsyncClient:
        async def aclose(self) -> None:
            closed.append(True)

    backend = RemoteSandboxExec("http://test")
    backend._client = _TrackingAsyncClient()  # type: ignore[assignment]
    backend._owns_client = False
    asyncio.run(backend.aclose())
    assert closed == []


def test_remote_sandbox_exec_raises_on_error_response() -> None:
    """exec() raises RemoteRuntimeError on a non-success response (lines 199-204)."""
    from orcheo.sandbox.remote import RemoteRuntimeError

    async def go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "exec failed"})

        transport = httpx.ASGITransport  # not used — mock directly
        async_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        backend = RemoteSandboxExec("http://test", client=async_client)
        try:
            await backend.exec(
                "sb-1",
                ["echo", "hi"],
                cwd=None,
                env=None,
                timeout_seconds=None,
            )
        finally:
            await async_client.aclose()

    with pytest.raises(RemoteRuntimeError, match="exec failed"):
        asyncio.run(go())


def test_remote_sandbox_exec_non_json_error_uses_text() -> None:
    """exec() with a non-JSON error body uses raw text in the message (lines 199-204)."""
    from orcheo.sandbox.remote import RemoteRuntimeError

    async def go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                502, content=b"Bad Gateway", headers={"content-type": "text/plain"}
            )

        async_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        backend = RemoteSandboxExec("http://test", client=async_client)
        try:
            await backend.exec(
                "sb-1", ["cmd"], cwd=None, env=None, timeout_seconds=None
            )
        finally:
            await async_client.aclose()

    with pytest.raises(RemoteRuntimeError):
        asyncio.run(go())


# ---------------------------------------------------------------------------
# RemoteSandboxRunner additional coverage (lines 264-265, 284-292)
# ---------------------------------------------------------------------------


def test_remote_sandbox_runner_aclose_owned_client() -> None:
    """aclose() closes the underlying client when owned (lines 264-265)."""
    closed: list[bool] = []

    class _TrackingAsyncClient:
        async def aclose(self) -> None:
            closed.append(True)

    runner = RemoteSandboxRunner("http://test")
    runner._client = _TrackingAsyncClient()  # type: ignore[assignment]
    runner._owns_client = True
    asyncio.run(runner.aclose())
    assert closed == [True]


def test_remote_sandbox_runner_aclose_unowned_client_skips() -> None:
    """aclose() skips disposal when not owning the client."""
    closed: list[bool] = []

    class _TrackingAsyncClient:
        async def aclose(self) -> None:
            closed.append(True)

    runner = RemoteSandboxRunner("http://test")
    runner._client = _TrackingAsyncClient()  # type: ignore[assignment]
    runner._owns_client = False
    asyncio.run(runner.aclose())
    assert closed == []


def test_remote_sandbox_runner_raises_on_error_response() -> None:
    """execute() raises RemoteRuntimeError on a non-success response (lines 284-292)."""
    from orcheo.sandbox.remote import RemoteRuntimeError
    from orcheo.sandbox.models import SandboxLease, SandboxState

    async def go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "dispatch failed"})

        async_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        runner = RemoteSandboxRunner("http://test", client=async_client)
        lease = SandboxLease(
            lease_id="l1",
            workspace_id="ws",
            sandbox_id="sb-err",
            state=SandboxState.IN_USE,
        )
        spec = WorkflowRunSpec(
            run_id="r-err",
            workspace_id="ws",
            workflow_definition={},
            inputs={},
            node_types=(),
            runnable_config={},
            state_config={},
        )
        try:
            await runner.execute(lease, spec, "token")
        finally:
            await async_client.aclose()

    with pytest.raises(RemoteRuntimeError, match="dispatch failed"):
        asyncio.run(go())


def test_remote_sandbox_runner_non_json_error_uses_text() -> None:
    """execute() with a non-JSON error body uses raw text (lines 284-292)."""
    from orcheo.sandbox.remote import RemoteRuntimeError
    from orcheo.sandbox.models import SandboxLease, SandboxState

    async def go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                503,
                content=b"Service Unavailable",
                headers={"content-type": "text/plain"},
            )

        async_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        runner = RemoteSandboxRunner("http://test", client=async_client)
        lease = SandboxLease(
            lease_id="l2",
            workspace_id="ws",
            sandbox_id="sb-503",
            state=SandboxState.IN_USE,
        )
        spec = WorkflowRunSpec(
            run_id="r-503",
            workspace_id="ws",
            workflow_definition={},
            inputs={},
            node_types=(),
            runnable_config={},
            state_config={},
        )
        try:
            await runner.execute(lease, spec, "tok")
        finally:
            await async_client.aclose()

    with pytest.raises(RemoteRuntimeError):
        asyncio.run(go())


# ---------------------------------------------------------------------------
# serialize_workflow_run_result (lines 304-306) — non-dataclass path
# ---------------------------------------------------------------------------


def test_serialize_workflow_run_result_non_dataclass() -> None:
    """serialize_workflow_run_result works for non-dataclass objects (lines 304-306)."""
    from orcheo.sandbox.remote import serialize_workflow_run_result

    result = WorkflowRunResult(
        run_id="r-serial",
        status="succeeded",
        outputs={"x": 1},
        error=None,
    )
    # WorkflowRunResult IS a dataclass, so patch is_dataclass to return False.
    import dataclasses

    original = dataclasses.is_dataclass

    def _fake_is_dataclass(obj: object) -> bool:
        return False

    import orcheo.sandbox.remote as remote_module

    original_fn = remote_module.is_dataclass
    remote_module.is_dataclass = _fake_is_dataclass  # type: ignore[assignment]
    try:
        serialized = serialize_workflow_run_result(result)
    finally:
        remote_module.is_dataclass = original_fn  # type: ignore[assignment]

    assert serialized["run_id"] == "r-serial"
    assert serialized["status"] == "succeeded"
    assert serialized["outputs"] == {"x": 1}
    assert serialized["error"] is None


# ---------------------------------------------------------------------------
# service.py additional coverage
# ---------------------------------------------------------------------------


def test_container_executor_init_and_invoker_init() -> None:
    """ContainerExecutor and WorkflowSandboxInvoker can be constructed directly (line 183)."""
    from orcheo.sandbox.service import ContainerExecutor, WorkflowSandboxInvoker

    executor = ContainerExecutor()
    invoker = WorkflowSandboxInvoker(executor)
    assert invoker._executor is executor


def test_last_json_line_returns_last_valid_json() -> None:
    """_last_json_line returns the last {...} line (lines 268-274)."""
    from orcheo.sandbox.service import _last_json_line

    blob = 'some log line\n{"status": "ok"}\nmore log\n{"status": "done"}\n'
    assert _last_json_line(blob) == '{"status": "done"}'


def test_last_json_line_skips_empty_lines() -> None:
    """_last_json_line skips blank lines when searching (lines 268-274)."""
    from orcheo.sandbox.service import _last_json_line

    blob = '{"status": "ok"}\n\n   \n'
    assert _last_json_line(blob) == '{"status": "ok"}'


def test_last_json_line_returns_none_when_no_json() -> None:
    """_last_json_line returns None when no JSON line exists (lines 268-274)."""
    from orcheo.sandbox.service import _last_json_line

    assert _last_json_line("no json here\nplain text") is None
    assert _last_json_line("") is None


def test_parse_workflow_spec_raises_400_on_missing_field() -> None:
    """_parse_workflow_spec raises HTTPException 400 on KeyError (lines 289-290)."""
    from orcheo.sandbox.service import WorkflowDispatchPayload, _parse_workflow_spec

    payload = WorkflowDispatchPayload(spec={"workspace_id": "ws"}, broker_token="tok")
    # Missing "run_id" → KeyError → 400.
    with pytest.raises(Exception) as exc_info:
        _parse_workflow_spec(payload)
    assert exc_info.value.status_code == 400  # type: ignore[attr-defined]
    assert "run_id" in exc_info.value.detail  # type: ignore[attr-defined]


def test_provision_endpoint_returns_500_on_runtime_failure(
    app_with_fakes: tuple[
        TestClient, InMemoryContainerRuntime, _FakeExecutor, _FakeInvoker
    ],
) -> None:
    """POST /containers returns 500 when runtime.start raises (lines 308-310)."""
    client, runtime, _executor, _invoker = app_with_fakes

    class _BoomRuntime(InMemoryContainerRuntime):
        def start(self, spec: ContainerSpec) -> Any:  # type: ignore[override]
            raise RuntimeError("docker is gone")

    boom_app = build_service_app(
        runtime=_BoomRuntime(),
        executor=_executor,
        invoker=_invoker,
        settings=SandboxSettings(
            credential_broker_url="http://10.99.0.2:9091/credentials/resolve"
        ),
        control_token="control-test-token",
    )
    boom_client = TestClient(boom_app, headers=_CONTROL_HEADERS)
    spec = ContainerSpec(image="img", workspace_id="ws")
    response = boom_client.post("/internal/containers", json=_spec_payload(spec))
    assert response.status_code == 500
    assert "provision failed" in response.json()["detail"]


def test_stop_endpoint_returns_500_on_runtime_failure(
    app_with_fakes: tuple[
        TestClient, InMemoryContainerRuntime, _FakeExecutor, _FakeInvoker
    ],
) -> None:
    """DELETE /containers/{id} returns 500 when runtime.stop raises (lines 332-334)."""
    client, runtime, executor, invoker = app_with_fakes

    spec = ContainerSpec(image="img", workspace_id="ws")
    provision = client.post("/internal/containers", json=_spec_payload(spec))
    container_id = provision.json()["container_id"]

    class _BoomStopRuntime(InMemoryContainerRuntime):
        def stop(self, handle: Any) -> None:  # type: ignore[override]
            raise RuntimeError("kill failed")

    boom_runtime = _BoomStopRuntime()
    # Pre-populate the handles so the new app thinks the container exists.
    handle, _ = runtime.started[0]
    boom_runtime._running.add(handle.container_id)
    boom_runtime.started.append(runtime.started[0])

    boom_app = build_service_app(
        runtime=boom_runtime,
        executor=executor,
        invoker=invoker,
        settings=SandboxSettings(
            credential_broker_url="http://10.99.0.2:9091/credentials/resolve"
        ),
        control_token="control-test-token",
    )
    boom_client = TestClient(boom_app, headers=_CONTROL_HEADERS)
    # First provision to register the handle in the new app's state.
    boom_provision = boom_client.post("/internal/containers", json=_spec_payload(spec))
    boom_container_id = boom_provision.json()["container_id"]

    response = boom_client.delete(f"/internal/containers/{boom_container_id}")
    assert response.status_code == 500
    assert "stop failed" in response.json()["detail"]


def test_inspect_endpoint_returns_running_status(
    app_with_fakes: tuple[
        TestClient, InMemoryContainerRuntime, _FakeExecutor, _FakeInvoker
    ],
) -> None:
    """GET /containers/{id} returns the running status (line 343)."""
    client, runtime, _executor, _invoker = app_with_fakes
    spec = ContainerSpec(image="img", workspace_id="ws")
    provision = client.post("/internal/containers", json=_spec_payload(spec))
    container_id = provision.json()["container_id"]

    response = client.get(f"/internal/containers/{container_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["running"] is True
    assert data["container_id"] == container_id


def test_inspect_endpoint_returns_404_for_unknown_container(
    app_with_fakes: tuple[
        TestClient, InMemoryContainerRuntime, _FakeExecutor, _FakeInvoker
    ],
) -> None:
    """GET /containers/{id} returns 404 when container is not registered."""
    client, _runtime, _executor, _invoker = app_with_fakes
    response = client.get("/internal/containers/unknown-id")
    assert response.status_code == 404


def test_credential_relay_without_optional_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credential relay works when Authorization and X-Orcheo-Workspace are absent (390->392, 392->394)."""
    client = TestClient(build_credential_relay_app())

    class _MinimalAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _MinimalAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> httpx.Response:
            # Neither Authorization nor X-Orcheo-Workspace should be in headers.
            assert "Authorization" not in headers
            assert "X-Orcheo-Workspace" not in headers
            return httpx.Response(200, json={"value": "ok"})

    monkeypatch.setenv(
        "ORCHEO_CREDENTIAL_BROKER_FORWARD_URL",
        "http://backend/internal/credentials/resolve",
    )
    monkeypatch.setattr(service_module.httpx, "AsyncClient", _MinimalAsyncClient)

    response = client.post(
        "/credentials/resolve",
        json={"run_id": "r-2", "credential_name": "key"},
        # No Authorization or X-Orcheo-Workspace headers
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# WorkflowSandboxInvoker.invoke() additional paths (lines 194-258)
# ---------------------------------------------------------------------------


def test_invoker_returns_failed_on_timeout() -> None:
    """invoke() returns a failed WorkflowRunResult when executor times out."""

    async def go() -> WorkflowRunResult:
        executor = _FakeExecutor()
        executor.result = asyncio.get_event_loop()
        # Override to return timed_out=True.
        from orcheo.external_agents.models import ProcessExecutionResult

        timed_out_result = ProcessExecutionResult(
            command=["sh"],
            stdout="",
            stderr="",
            exit_code=None,
            timed_out=True,
            duration_seconds=30.0,
        )
        executor.result = timed_out_result

        from orcheo.sandbox.service import WorkflowSandboxInvoker
        from orcheo.sandbox.workflow import WorkflowRunSpec

        invoker = WorkflowSandboxInvoker(executor)
        spec = WorkflowRunSpec(
            run_id="r-timeout",
            workspace_id="ws",
            workflow_definition={},
            inputs={},
            node_types=(),
            runnable_config={},
            state_config={},
        )
        return await invoker.invoke("sb-1", spec, "token")

    result = asyncio.run(go())
    assert result.status == "failed"
    assert "timed out" in (result.error or "")


def test_invoker_returns_failed_on_non_zero_exit() -> None:
    """invoke() returns failed when the runner exits with non-zero code."""

    async def go() -> WorkflowRunResult:
        from orcheo.external_agents.models import ProcessExecutionResult

        executor = _FakeExecutor()
        executor.result = ProcessExecutionResult(
            command=["sh"],
            stdout="",
            stderr="runner crashed",
            exit_code=1,
            timed_out=False,
            duration_seconds=0.1,
        )
        from orcheo.sandbox.service import WorkflowSandboxInvoker
        from orcheo.sandbox.workflow import WorkflowRunSpec

        invoker = WorkflowSandboxInvoker(executor)
        spec = WorkflowRunSpec(
            run_id="r-exitcode",
            workspace_id="ws",
            workflow_definition={},
            inputs={},
            node_types=(),
            runnable_config={},
            state_config={},
        )
        return await invoker.invoke("sb-1", spec, "token")

    result = asyncio.run(go())
    assert result.status == "failed"
    assert "exited with" in (result.error or "") or result.error is not None


def test_invoker_returns_failed_when_no_json_output() -> None:
    """invoke() returns failed when runner produces no JSON on stdout."""

    async def go() -> WorkflowRunResult:
        from orcheo.external_agents.models import ProcessExecutionResult

        executor = _FakeExecutor()
        executor.result = ProcessExecutionResult(
            command=["sh"],
            stdout="some non-json log output",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
        )
        from orcheo.sandbox.service import WorkflowSandboxInvoker
        from orcheo.sandbox.workflow import WorkflowRunSpec

        invoker = WorkflowSandboxInvoker(executor)
        spec = WorkflowRunSpec(
            run_id="r-nojson",
            workspace_id="ws",
            workflow_definition={},
            inputs={},
            node_types=(),
            runnable_config={},
            state_config={},
        )
        return await invoker.invoke("sb-1", spec, "token")

    result = asyncio.run(go())
    assert result.status == "failed"
    assert "no JSON output" in (result.error or "")


def test_invoker_returns_failed_on_json_decode_error() -> None:
    """invoke() returns failed when runner output is invalid JSON."""

    async def go() -> WorkflowRunResult:
        from orcheo.external_agents.models import ProcessExecutionResult

        executor = _FakeExecutor()
        executor.result = ProcessExecutionResult(
            command=["sh"],
            stdout="{not valid json}",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
        )
        from orcheo.sandbox.service import WorkflowSandboxInvoker
        from orcheo.sandbox.workflow import WorkflowRunSpec

        invoker = WorkflowSandboxInvoker(executor)
        spec = WorkflowRunSpec(
            run_id="r-badjson",
            workspace_id="ws",
            workflow_definition={},
            inputs={},
            node_types=(),
            runnable_config={},
            state_config={},
        )
        return await invoker.invoke("sb-1", spec, "token")

    result = asyncio.run(go())
    assert result.status == "failed"
    assert "parse" in (result.error or "").lower()


def test_invoker_returns_succeeded_on_valid_json_output() -> None:
    """invoke() parses a valid JSON result from runner stdout."""
    import json

    async def go() -> WorkflowRunResult:
        from orcheo.external_agents.models import ProcessExecutionResult

        result_json = json.dumps(
            {"status": "succeeded", "outputs": {"answer": 42}, "error": None}
        )
        executor = _FakeExecutor()
        executor.result = ProcessExecutionResult(
            command=["sh"],
            stdout=f"some logs\n{result_json}\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
        )
        from orcheo.sandbox.service import WorkflowSandboxInvoker
        from orcheo.sandbox.workflow import WorkflowRunSpec

        invoker = WorkflowSandboxInvoker(executor)
        spec = WorkflowRunSpec(
            run_id="r-ok",
            workspace_id="ws",
            workflow_definition={},
            inputs={},
            node_types=(),
            runnable_config={},
            state_config={},
        )
        return await invoker.invoke("sb-1", spec, "token")

    result = asyncio.run(go())
    assert result.status == "succeeded"
    assert result.outputs == {"answer": 42}


# ---------------------------------------------------------------------------
# ContainerExecutor.exec() direct tests (lines 125-161)
# ---------------------------------------------------------------------------


def test_container_executor_exec_builds_argv_and_returns_result() -> None:
    """ContainerExecutor.exec() runs docker exec with cwd and env (lines 125-161)."""
    import unittest.mock as mock

    async def go() -> ProcessExecutionResult:
        from orcheo.sandbox.service import ContainerExecutor, ExecRequest

        executor = ContainerExecutor()
        request = ExecRequest(
            command=["echo", "hi"],
            cwd="/workspace",
            env={"FOO": "bar"},
            stdin="payload",
            timeout_seconds=10.0,
        )

        fake_process = mock.AsyncMock()
        observed: dict[str, bytes | None] = {}

        async def _communicate(input: bytes | None = None) -> tuple[bytes, bytes]:
            observed["input"] = input
            return (b"hi\n", b"")

        fake_process.communicate = mock.AsyncMock(side_effect=_communicate)
        fake_process.returncode = 0

        with mock.patch(
            "orcheo.sandbox.service.asyncio.create_subprocess_exec",
            return_value=fake_process,
        ) as patched:
            result = await executor.exec("ctr-1", request)

        call_args = patched.call_args[0]
        assert "docker" in call_args
        assert "exec" in call_args
        assert "-i" in call_args
        assert "-w" in call_args
        assert "/workspace" in call_args
        assert "-e" in call_args
        assert "FOO=bar" in call_args
        assert "echo" in call_args
        assert observed["input"] == b"payload"
        assert result.stdout == "hi\n"
        assert result.exit_code == 0
        return result

    asyncio.run(go())


def test_container_executor_exec_no_cwd_no_env() -> None:
    """ContainerExecutor.exec() omits -w and -e when cwd/env are absent."""
    import unittest.mock as mock

    async def go() -> ProcessExecutionResult:
        from orcheo.sandbox.service import ContainerExecutor, ExecRequest

        executor = ContainerExecutor()
        request = ExecRequest(
            command=["ls"],
            cwd=None,
            env=None,
            timeout_seconds=5.0,
        )

        fake_process = mock.AsyncMock()
        fake_process.communicate = mock.AsyncMock(return_value=(b"", b""))
        fake_process.returncode = 0

        with mock.patch(
            "orcheo.sandbox.service.asyncio.create_subprocess_exec",
            return_value=fake_process,
        ) as patched:
            result = await executor.exec("ctr-2", request)

        call_args = patched.call_args[0]
        assert "-w" not in call_args
        assert "-e" not in call_args
        assert result.exit_code == 0
        return result

    asyncio.run(go())


def test_container_executor_exec_handles_timeout() -> None:
    """ContainerExecutor.exec() kills the process on TimeoutError (lines 155-159)."""
    import unittest.mock as mock

    async def go() -> ProcessExecutionResult:
        from orcheo.sandbox.service import ContainerExecutor, ExecRequest

        executor = ContainerExecutor()
        request = ExecRequest(
            command=["sleep", "9999"],
            cwd=None,
            env=None,
            timeout_seconds=0.001,
        )

        killed = []

        fake_process = mock.AsyncMock()
        fake_process.kill = mock.MagicMock(side_effect=lambda: killed.append(True))
        fake_process.communicate = mock.AsyncMock(return_value=(b"", b""))
        fake_process.returncode = None

        async def _wait_for_raises(coro: Any, *, timeout: float) -> Any:
            coro.close()
            raise TimeoutError

        with mock.patch(
            "orcheo.sandbox.service.asyncio.create_subprocess_exec",
            return_value=fake_process,
        ):
            with mock.patch(
                "orcheo.sandbox.service.asyncio.wait_for",
                side_effect=_wait_for_raises,
            ):
                result = await executor.exec("ctr-3", request)

        assert result.timed_out is True
        assert result.exit_code is None
        assert killed  # kill() was called
        return result

    asyncio.run(go())


# ---------------------------------------------------------------------------
# serialize_workflow_run_result — dataclass path (line 305 in remote.py)
# ---------------------------------------------------------------------------


def test_serialize_workflow_run_result_with_real_dataclass() -> None:
    """serialize_workflow_run_result returns asdict() when result is a real dataclass (line 305)."""
    from orcheo.sandbox.remote import serialize_workflow_run_result
    from orcheo.sandbox.workflow import WorkflowRunResult

    result = WorkflowRunResult(
        run_id="r-dc",
        status="succeeded",
        outputs={"k": "v"},
        error=None,
    )
    serialized = serialize_workflow_run_result(result)
    assert serialized["run_id"] == "r-dc"
    assert serialized["status"] == "succeeded"
    assert serialized["outputs"] == {"k": "v"}
    assert serialized["error"] is None


# ---------------------------------------------------------------------------
# Central lease API — service side (/internal/leases)
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_manager() -> tuple[
    TestClient, InMemoryContainerRuntime, SandboxRuntimeManager
]:
    """Build the service app and return the embedded manager for inspection."""
    runtime = InMemoryContainerRuntime()
    settings = SandboxSettings(
        credential_broker_url="http://10.99.0.2:9091/credentials/resolve"
    )
    manager = SandboxRuntimeManager(runtime=runtime, settings=settings)
    executor = _FakeExecutor()
    invoker = _FakeInvoker()
    app = build_service_app(
        runtime=runtime,
        executor=executor,
        invoker=invoker,
        settings=settings,
        control_token="control-test-token",
        manager=manager,
    )
    return TestClient(app, headers=_CONTROL_HEADERS), runtime, manager


def test_lease_acquire_returns_in_use_lease(
    app_with_manager: tuple[
        TestClient, InMemoryContainerRuntime, SandboxRuntimeManager
    ],
) -> None:
    """POST /internal/leases provisions a container and returns a lease."""
    client, runtime, manager = app_with_manager
    response = client.post("/internal/leases", json={"workspace_id": "ws-lease"})
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["workspace_id"] == "ws-lease"
    assert data["state"] == SandboxState.IN_USE.value
    assert data["sandbox_id"]
    assert data["lease_id"]
    assert len(runtime.started) == 1
    assert manager.get_lease(data["lease_id"]) is not None


def test_lease_acquire_with_run_id(
    app_with_manager: tuple[
        TestClient, InMemoryContainerRuntime, SandboxRuntimeManager
    ],
) -> None:
    """POST /internal/leases forwards run_id to the manager."""
    client, _runtime, manager = app_with_manager
    response = client.post(
        "/internal/leases", json={"workspace_id": "ws-rid", "run_id": "run-abc"}
    )
    assert response.status_code == 201, response.text


def test_lease_release_returns_lease_to_pool(
    app_with_manager: tuple[
        TestClient, InMemoryContainerRuntime, SandboxRuntimeManager
    ],
) -> None:
    """POST /internal/leases/{id}/release returns the lease to the warm pool."""
    client, runtime, manager = app_with_manager
    acquire = client.post("/internal/leases", json={"workspace_id": "ws-rel"})
    assert acquire.status_code == 201
    lease_id = acquire.json()["lease_id"]

    release = client.post(f"/internal/leases/{lease_id}/release")
    assert release.status_code == 204

    # The container was not stopped — it went to the pool.
    assert len(runtime.stopped) == 0
    lease = manager.get_lease(lease_id)
    assert lease is not None
    assert lease.state is SandboxState.READY

    # A second acquire should warm-reuse the same container.
    acquire2 = client.post("/internal/leases", json={"workspace_id": "ws-rel"})
    assert acquire2.status_code == 201
    assert acquire2.json()["lease_id"] == lease_id
    assert len(runtime.started) == 1  # no new container


def test_lease_destroy_stops_container(
    app_with_manager: tuple[
        TestClient, InMemoryContainerRuntime, SandboxRuntimeManager
    ],
) -> None:
    """DELETE /internal/leases/{id} tears down the container."""
    client, runtime, manager = app_with_manager
    acquire = client.post("/internal/leases", json={"workspace_id": "ws-del"})
    lease_id = acquire.json()["lease_id"]

    destroy = client.delete(f"/internal/leases/{lease_id}")
    assert destroy.status_code == 204
    assert len(runtime.stopped) == 1
    assert manager.get_lease(lease_id) is None


def test_lease_destroy_is_idempotent(
    app_with_manager: tuple[
        TestClient, InMemoryContainerRuntime, SandboxRuntimeManager
    ],
) -> None:
    """DELETE /internal/leases/{id} returns 204 even when already gone."""
    client, _runtime, _manager = app_with_manager
    response = client.delete("/internal/leases/no-such-lease")
    assert response.status_code == 204


def test_lease_release_returns_404_for_unknown_lease(
    app_with_manager: tuple[
        TestClient, InMemoryContainerRuntime, SandboxRuntimeManager
    ],
) -> None:
    """POST /internal/leases/{id}/release returns 404 when lease is unknown."""
    client, _runtime, _manager = app_with_manager
    response = client.post("/internal/leases/no-such-lease/release")
    assert response.status_code == 404


def test_lease_acquire_409_when_pool_exhausted(
    app_with_manager: tuple[
        TestClient, InMemoryContainerRuntime, SandboxRuntimeManager
    ],
) -> None:
    """POST /internal/leases returns 409 when the workspace pool is exhausted."""
    client, _runtime, manager = app_with_manager
    # Set pool_max=1 for this workspace.
    from orcheo.sandbox.models import WorkspaceRuntimePool

    manager.configure_workspace(
        WorkspaceRuntimePool(workspace_id="ws-full", pool_max=1)
    )
    client.post("/internal/leases", json={"workspace_id": "ws-full"})
    response = client.post("/internal/leases", json={"workspace_id": "ws-full"})
    assert response.status_code == 409


def test_lease_routes_require_control_token(
    app_with_manager: tuple[
        TestClient, InMemoryContainerRuntime, SandboxRuntimeManager
    ],
) -> None:
    """Lease endpoints fail closed without the control token."""
    client, _runtime, _manager = app_with_manager
    unauthed = TestClient(client.app)
    assert (
        unauthed.post("/internal/leases", json={"workspace_id": "ws"}).status_code
        == 401
    )
    assert unauthed.post("/internal/leases/x/release").status_code == 401
    assert unauthed.delete("/internal/leases/x").status_code == 401


# ---------------------------------------------------------------------------
# RemoteSandboxManager — client side
# ---------------------------------------------------------------------------


def test_remote_sandbox_manager_acquire_release_destroy(
    app_with_manager: tuple[
        TestClient, InMemoryContainerRuntime, SandboxRuntimeManager
    ],
) -> None:
    """RemoteSandboxManager round-trips through the service lease API.

    Uses the TestClient as the HTTP transport so the sync client talks to the
    ASGI app without needing a real server.
    """
    test_client, runtime, _manager = app_with_manager

    class _TestClientTransport(httpx.BaseTransport):
        """Forward httpx requests to a Starlette TestClient."""

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            resp = test_client.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(
                status_code=resp.status_code,
                headers=dict(resp.headers),
                content=resp.content,
            )

    sync_client = httpx.Client(
        transport=_TestClientTransport(), base_url="http://testserver"
    )
    remote = RemoteSandboxManager(
        "http://testserver",
        control_token="control-test-token",
        client=sync_client,
    )

    lease = remote.acquire("ws-remote", run_id="r-1")
    assert lease.state is SandboxState.IN_USE
    assert lease.workspace_id == "ws-remote"
    assert len(runtime.started) == 1

    # Release returns to warm pool — no container stop.
    remote.release(lease)
    assert lease.state is SandboxState.READY
    assert len(runtime.stopped) == 0

    # Acquire again — warm reuse.
    lease2 = remote.acquire("ws-remote")
    assert lease2.lease_id == lease.lease_id
    assert len(runtime.started) == 1

    # Destroy terminates the container.
    remote.destroy(lease2)
    assert lease2.state is SandboxState.DESTROYED
    assert len(runtime.stopped) == 1

    sync_client.close()


def test_remote_sandbox_manager_destroy_idempotent() -> None:
    """RemoteSandboxManager.destroy() is idempotent for unknown leases."""
    from orcheo.sandbox.models import SandboxLease

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test")
    remote = RemoteSandboxManager("http://test", client=client)
    lease = SandboxLease(
        lease_id="gone", workspace_id="ws", sandbox_id="sb", state=SandboxState.IN_USE
    )
    remote.destroy(lease)  # must not raise


def test_remote_sandbox_manager_acquire_raises_on_pool_exhausted() -> None:
    """RemoteSandboxManager.acquire() raises SandboxAcquireError on 409."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "pool exhausted"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test")
    remote = RemoteSandboxManager("http://test", client=client)
    with pytest.raises(SandboxAcquireError, match="pool exhausted"):
        remote.acquire("ws")


def test_remote_sandbox_manager_acquire_raises_on_server_error() -> None:
    """RemoteSandboxManager.acquire() raises RemoteRuntimeError on 5xx."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "internal error"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test")
    remote = RemoteSandboxManager("http://test", client=client)
    with pytest.raises(RemoteRuntimeError, match="acquire lease failed"):
        remote.acquire("ws")


def test_remote_sandbox_manager_release_idempotent_on_404() -> None:
    """RemoteSandboxManager.release() is idempotent when the lease is gone."""
    from orcheo.sandbox.models import SandboxLease

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test")
    remote = RemoteSandboxManager("http://test", client=client)
    lease = SandboxLease(
        lease_id="gone", workspace_id="ws", sandbox_id="sb", state=SandboxState.IN_USE
    )
    remote.release(lease)  # must not raise
