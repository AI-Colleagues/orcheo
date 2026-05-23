"""Tests for the sandbox-runtime HTTP service and its remote clients."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any
import httpx
import pytest
from fastapi.testclient import TestClient
from orcheo.sandbox import service as service_module
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.sandbox.remote import (
    RemoteContainerRuntime,
    RemoteSandboxExec,
    RemoteSandboxRunner,
)
from orcheo.sandbox.runtime import ContainerSpec, InMemoryContainerRuntime
from orcheo.sandbox.service import (
    ContainerExecutor,
    ExecRequest,
    WorkflowSandboxInvoker,
    build_service_app,
)
from orcheo.sandbox.workflow import WorkflowRunResult, WorkflowRunSpec


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
    app = build_service_app(runtime=runtime, executor=executor, invoker=invoker)
    return TestClient(app), runtime, executor, invoker


def test_healthz(app_with_fakes: tuple[TestClient, Any, Any, Any]) -> None:
    """The service exposes a /healthz endpoint."""
    client, *_ = app_with_fakes
    assert client.get("/healthz").json() == {"status": "ok"}


def test_credential_relay_forwards_broker_request(
    app_with_fakes: tuple[TestClient, Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sandbox credential requests cross the runtime relay unchanged."""
    client, *_ = app_with_fakes
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
    )
    response = client.post("/containers", json=_spec_payload(spec))
    assert response.status_code == 201, response.text
    container_id = response.json()["container_id"]
    assert runtime.started, "runtime.start was not invoked"

    inspect = client.get(f"/containers/{container_id}")
    assert inspect.status_code == 200
    assert inspect.json()["running"] is True

    stop = client.delete(f"/containers/{container_id}")
    assert stop.status_code == 204
    assert runtime.stopped, "runtime.stop was not invoked"

    # Stopping again is a 404 because the container is no longer running.
    assert client.delete(f"/containers/{container_id}").status_code == 404


def test_stop_works_without_cached_handle(
    app_with_fakes: tuple[
        TestClient, InMemoryContainerRuntime, _FakeExecutor, _FakeInvoker
    ],
) -> None:
    """DELETE /containers/{id} still stops a running container after restart."""
    client, runtime, executor, invoker = app_with_fakes
    spec = ContainerSpec(image="img", workspace_id="ws")
    provision = client.post("/containers", json=_spec_payload(spec))
    container_id = provision.json()["container_id"]

    # Simulate runtime-service restart by rebuilding the app with the same runtime.
    running_handle, _ = runtime.started[0]
    assert runtime.is_running(running_handle) is True
    restarted_client = TestClient(
        build_service_app(runtime=runtime, executor=executor, invoker=invoker)
    )

    stop = restarted_client.delete(f"/containers/{container_id}")
    assert stop.status_code == 204
    assert runtime.is_running(running_handle) is False


def test_exec_endpoint_calls_executor(
    app_with_fakes: tuple[TestClient, Any, _FakeExecutor, Any],
) -> None:
    """POST /sandboxes/{id}/exec routes through ContainerExecutor.exec."""
    client, _runtime, executor, _invoker = app_with_fakes
    response = client.post(
        "/sandboxes/sb-1/exec",
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
    response = client.post("/sandboxes/sb-1/dispatch_workflow", json=payload)
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


def test_remote_container_runtime_round_trip() -> None:
    """RemoteContainerRuntime parses runtime-service responses correctly."""
    state: dict[str, Any] = {"running": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/containers":
            return httpx.Response(
                201,
                json={
                    "container_id": "c-1",
                    "image": "img",
                    "workspace_id": "ws-remote",
                    "runtime": "runsc",
                },
            )
        if request.method == "GET" and request.url.path == "/containers/c-1":
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
        if request.method == "DELETE" and request.url.path == "/containers/c-1":
            state["running"] = False
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    sync_client = httpx.Client(transport=transport, base_url="http://test")
    remote = RemoteContainerRuntime("http://test", client=sync_client)
    try:
        handle = remote.start(ContainerSpec(image="img", workspace_id="ws-remote"))
        assert handle.workspace_id == "ws-remote"
        assert remote.is_running(handle) is True
        remote.stop(handle)
        assert state["running"] is False
    finally:
        remote.close()


def test_remote_exec_returns_process_result(
    app_with_fakes: tuple[TestClient, Any, _FakeExecutor, Any],
) -> None:
    """RemoteSandboxExec parses the service's JSON into ProcessExecutionResult."""
    client, _runtime, executor, _invoker = app_with_fakes
    transport = httpx.ASGITransport(app=client.app)
    async_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    backend = RemoteSandboxExec("http://testserver", client=async_client)

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
    runner = RemoteSandboxRunner("http://testserver", client=async_client)

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
    """Render a ``ContainerSpec`` as the JSON the service expects."""
    return {
        "image": spec.image,
        "workspace_id": spec.workspace_id,
        "runtime": spec.runtime,
        "command": list(spec.command),
        "environment": dict(spec.environment),
        "cpu_limit": spec.cpu_limit,
        "memory_limit": spec.memory_limit,
        "pid_limit": spec.pid_limit,
        "scratch_size": spec.scratch_size,
        "user": spec.user,
        "network_mode": spec.network_mode,
        "read_only_root": spec.read_only_root,
        "cap_drop": list(spec.cap_drop),
        "no_new_privileges": spec.no_new_privileges,
        "labels": dict(spec.labels),
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
    )
    boom_client = TestClient(boom_app)
    spec = ContainerSpec(image="img", workspace_id="ws")
    response = boom_client.post("/containers", json=_spec_payload(spec))
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
    provision = client.post("/containers", json=_spec_payload(spec))
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
    )
    boom_client = TestClient(boom_app)
    # First provision to register the handle in the new app's state.
    boom_provision = boom_client.post("/containers", json=_spec_payload(spec))
    boom_container_id = boom_provision.json()["container_id"]

    response = boom_client.delete(f"/containers/{boom_container_id}")
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
    provision = client.post("/containers", json=_spec_payload(spec))
    container_id = provision.json()["container_id"]

    response = client.get(f"/containers/{container_id}")
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
    response = client.get("/containers/unknown-id")
    assert response.status_code == 404


def test_credential_relay_without_optional_headers(
    app_with_fakes: tuple[TestClient, Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credential relay works when Authorization and X-Orcheo-Workspace are absent (390->392, 392->394)."""
    client, *_ = app_with_fakes

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
            timeout_seconds=10.0,
        )

        fake_process = mock.AsyncMock()
        fake_process.communicate = mock.AsyncMock(return_value=(b"hi\n", b""))
        fake_process.returncode = 0

        with mock.patch(
            "orcheo.sandbox.service.asyncio.create_subprocess_exec",
            return_value=fake_process,
        ) as patched:
            result = await executor.exec("ctr-1", request)

        call_args = patched.call_args[0]
        assert "docker" in call_args
        assert "exec" in call_args
        assert "-w" in call_args
        assert "/workspace" in call_args
        assert "-e" in call_args
        assert "FOO=bar" in call_args
        assert "echo" in call_args
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
