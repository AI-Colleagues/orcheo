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
        "ORCHEO_CREDENTIAL_BROKER_URL",
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

    # Stopping again is a 404 because the handle is gone.
    assert client.delete(f"/containers/{container_id}").status_code == 404


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
