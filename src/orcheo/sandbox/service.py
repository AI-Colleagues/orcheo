"""HTTP service exposing the Sandbox Runtime Manager to backend / worker.

The Docker socket is root-equivalent on the host (design §Security
Considerations). To keep the socket off the backend and worker containers,
this service runs inside the dedicated ``sandbox-runtime`` container — the
only process that mounts ``/var/run/docker.sock`` — and exposes an HTTP API
that other Orcheo services call.

Endpoints
---------
- ``POST /containers``: provision a new sandbox container from a
  ``ContainerSpec`` payload. Returns the ``ContainerHandle``.
- ``DELETE /containers/{container_id}``: stop and remove a sandbox.
- ``GET /containers/{container_id}``: report whether the sandbox is running.
- ``POST /sandboxes/{sandbox_id}/exec``: run a one-shot command inside an
  existing sandbox (used by ``RemoteSandboxExec``).
- ``POST /sandboxes/{sandbox_id}/dispatch_workflow``: run a
  ``WorkflowRunSpec`` inside the sandbox and return its
  ``WorkflowRunResult``.

The service has no opinion on workspace pooling — that lives in the
:class:`SandboxRuntimeManager` on the caller side. The runtime service is a
thin wrapper around the container engine plus a ``docker exec`` shim.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import shlex
import time
from collections.abc import Mapping
from typing import Annotated, Any, Final
import httpx
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.sandbox.runtime import (
    ContainerHandle,
    ContainerRuntime,
    ContainerSpec,
    DockerContainerRuntime,
)
from orcheo.sandbox.workflow import WorkflowRunResult, WorkflowRunSpec


logger = logging.getLogger(__name__)


_WORKFLOW_RUNNER_MODULE: Final[str] = "orcheo.sandbox.workflow_runner"


class ContainerSpecPayload(BaseModel):
    """Wire-format ``ContainerSpec`` accepted by ``POST /containers``."""

    image: str
    workspace_id: str
    runtime: str = "runsc"
    command: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    cpu_limit: str = "1.0"
    memory_limit: str = "512m"
    pid_limit: int = 256
    scratch_size: str = "1g"
    user: str = "10001:10001"
    network_mode: str = "sandbox-egress"
    read_only_root: bool = True
    cap_drop: list[str] = Field(default_factory=lambda: ["ALL"])
    no_new_privileges: bool = True
    labels: dict[str, str] = Field(default_factory=dict)

    def to_spec(self) -> ContainerSpec:
        """Build a frozen ``ContainerSpec`` from the wire payload."""
        return ContainerSpec(
            image=self.image,
            workspace_id=self.workspace_id,
            runtime=self.runtime,
            command=tuple(self.command),
            environment=self.environment,
            cpu_limit=self.cpu_limit,
            memory_limit=self.memory_limit,
            pid_limit=self.pid_limit,
            scratch_size=self.scratch_size,
            user=self.user,
            network_mode=self.network_mode,
            read_only_root=self.read_only_root,
            cap_drop=tuple(self.cap_drop),
            no_new_privileges=self.no_new_privileges,
            labels=self.labels,
        )


class ExecRequest(BaseModel):
    """Wire-format ``docker exec`` request."""

    command: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout_seconds: float | None = None


class WorkflowDispatchPayload(BaseModel):
    """Wire-format payload for ``POST /sandboxes/{id}/dispatch_workflow``."""

    spec: dict[str, Any]
    broker_token: str = ""


class ContainerExecutor:
    """Run ``docker exec`` commands inside an existing sandbox container.

    The default implementation shells out to ``docker exec`` because the
    Docker SDK's ``exec_run`` does not give us a stable way to enforce a
    wall-clock timeout. Tests inject a fake.
    """

    async def exec(
        self,
        sandbox_id: str,
        request: ExecRequest,
    ) -> ProcessExecutionResult:
        """Run ``request.command`` inside ``sandbox_id`` via ``docker exec``."""
        argv: list[str] = ["docker", "exec"]
        if request.cwd is not None:
            argv.extend(["-w", request.cwd])
        if request.env:
            for key, value in request.env.items():
                argv.extend(["-e", f"{key}={value}"])
        argv.append(sandbox_id)
        argv.extend(request.command)

        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:  # pragma: no cover - dev environment
            msg = f"docker CLI is not available in the sandbox-runtime container: {exc}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=msg,
            ) from exc

        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=request.timeout_seconds,
            )
            exit_code: int | None = process.returncode
        except TimeoutError:
            timed_out = True
            process.kill()
            stdout, stderr = await process.communicate()
            exit_code = None

        return ProcessExecutionResult(
            command=request.command,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_seconds=time.monotonic() - started,
        )


class WorkflowSandboxInvoker:
    """Invoke the workflow runner inside a sandbox container and parse its result.

    The invoker pipes a single newline-delimited ``WorkflowRunSpec`` payload
    to ``python -m orcheo.sandbox.workflow_runner`` running in the sandbox
    and reads the response from stdout. The broker token is passed as the
    ``ORCHEO_BROKER_TOKEN`` env var so tenant code in the sandbox cannot see
    it on the command line via ``ps``.
    """

    def __init__(self, executor: ContainerExecutor) -> None:
        """Initialize the invoker."""
        self._executor = executor

    async def invoke(
        self,
        sandbox_id: str,
        spec: WorkflowRunSpec,
        broker_token: str,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkflowRunResult:
        """Send ``spec`` into the sandbox and parse its ``WorkflowRunResult``."""
        payload = json.dumps(
            {
                "workflow_definition": dict(spec.workflow_definition),
                "inputs": dict(spec.inputs),
                "run_id": spec.run_id,
                "workspace_id": spec.workspace_id,
                "runnable_config": dict(spec.runnable_config),
                "state_config": dict(spec.state_config),
            },
            separators=(",", ":"),
        )
        argv = [
            "sh",
            "-c",
            (
                "printf '%s\\n' "
                + shlex.quote(payload)
                + " | python -m "
                + _WORKFLOW_RUNNER_MODULE
            ),
        ]
        result = await self._executor.exec(
            sandbox_id,
            ExecRequest(
                command=argv,
                cwd=None,
                env={"ORCHEO_BROKER_TOKEN": broker_token} if broker_token else None,
                timeout_seconds=timeout_seconds,
            ),
        )
        if result.timed_out:
            return WorkflowRunResult(
                run_id=spec.run_id,
                status="failed",
                outputs={},
                error="workflow run timed out inside sandbox",
            )
        if result.exit_code not in (0, None):
            return WorkflowRunResult(
                run_id=spec.run_id,
                status="failed",
                outputs={},
                error=(
                    f"workflow runner exited with {result.exit_code}: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                ),
            )
        last_line = _last_json_line(result.stdout)
        if last_line is None:
            return WorkflowRunResult(
                run_id=spec.run_id,
                status="failed",
                outputs={},
                error="workflow runner produced no JSON output",
            )
        try:
            parsed: Mapping[str, Any] = json.loads(last_line)
        except json.JSONDecodeError as exc:
            return WorkflowRunResult(
                run_id=spec.run_id,
                status="failed",
                outputs={},
                error=f"could not parse workflow runner output: {exc}",
            )
        return WorkflowRunResult(
            run_id=spec.run_id,
            status=str(parsed.get("status", "failed")),
            outputs=dict(parsed.get("outputs") or {}),
            error=parsed.get("error"),
        )


def _last_json_line(blob: str) -> str | None:
    """Return the last non-empty line in ``blob`` that parses as JSON."""
    for line in reversed(blob.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        if candidate.startswith("{") and candidate.endswith("}"):
            return candidate
    return None


def _parse_workflow_spec(payload: WorkflowDispatchPayload) -> WorkflowRunSpec:
    """Parse a dispatch payload into a ``WorkflowRunSpec`` or raise 400."""
    try:
        return WorkflowRunSpec(
            run_id=str(payload.spec["run_id"]),
            workspace_id=str(payload.spec["workspace_id"]),
            workflow_definition=dict(payload.spec.get("workflow_definition") or {}),
            inputs=dict(payload.spec.get("inputs") or {}),
            node_types=tuple(payload.spec.get("node_types") or ()),
            runnable_config=dict(payload.spec.get("runnable_config") or {}),
            state_config=dict(payload.spec.get("state_config") or {}),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"missing field in spec: {exc}",
        ) from exc


def _register_container_routes(
    app: FastAPI,
    runtime: ContainerRuntime,
    handles: dict[str, ContainerHandle],
) -> None:
    """Register the container-lifecycle routes on ``app``."""

    @app.post("/containers", status_code=status.HTTP_201_CREATED)
    def provision(payload: ContainerSpecPayload) -> dict[str, str]:
        spec = payload.to_spec()
        try:
            handle = runtime.start(spec)
        except Exception as exc:  # noqa: BLE001 — surface runtime failures as 500
            logger.exception("Failed to provision sandbox for %s", spec.workspace_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"provision failed: {exc}",
            ) from exc
        handles[handle.container_id] = handle
        return handle.as_dict()

    @app.delete("/containers/{container_id}", status_code=status.HTTP_204_NO_CONTENT)
    def stop(container_id: str) -> None:
        handle = handles.pop(container_id, None)
        if handle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown container {container_id}",
            )
        try:
            runtime.stop(handle)
        except Exception as exc:  # noqa: BLE001 — surface runtime failures as 500
            logger.exception("Failed to stop sandbox %s", container_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"stop failed: {exc}",
            ) from exc

    @app.get("/containers/{container_id}")
    def inspect(container_id: str) -> dict[str, Any]:
        handle = handles.get(container_id)
        if handle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown container {container_id}",
            )
        return {**handle.as_dict(), "running": runtime.is_running(handle)}


def _register_sandbox_routes(
    app: FastAPI,
    executor: ContainerExecutor,
    invoker: WorkflowSandboxInvoker,
) -> None:
    """Register the in-sandbox exec / workflow routes on ``app``."""

    @app.post("/sandboxes/{sandbox_id}/exec")
    async def exec_in_sandbox(sandbox_id: str, payload: ExecRequest) -> dict[str, Any]:
        result = await executor.exec(sandbox_id, payload)
        return result.model_dump()

    @app.post("/sandboxes/{sandbox_id}/dispatch_workflow")
    async def dispatch_workflow(
        sandbox_id: str, payload: WorkflowDispatchPayload
    ) -> dict[str, Any]:
        spec = _parse_workflow_spec(payload)
        result = await invoker.invoke(sandbox_id, spec, payload.broker_token)
        return {
            "run_id": result.run_id,
            "status": result.status,
            "outputs": dict(result.outputs),
            "error": result.error,
        }


def _register_credential_relay_route(app: FastAPI) -> None:
    """Relay credential requests from sandboxes to the backend broker."""

    @app.post("/credentials/resolve")
    async def relay_credential_request(
        payload: dict[str, Any],
        authorization: Annotated[str | None, Header()] = None,
        x_orcheo_workspace: Annotated[str | None, Header()] = None,
    ) -> Response:
        broker_url = os.getenv(
            "ORCHEO_CREDENTIAL_BROKER_URL",
            "http://backend:2025/internal/credentials/resolve",
        )
        headers: dict[str, str] = {}
        if authorization is not None:
            headers["Authorization"] = authorization
        if x_orcheo_workspace is not None:
            headers["X-Orcheo-Workspace"] = x_orcheo_workspace
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(broker_url, json=payload, headers=headers)
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type"),
        )


def build_service_app(
    runtime: ContainerRuntime | None = None,
    executor: ContainerExecutor | None = None,
    invoker: WorkflowSandboxInvoker | None = None,
) -> FastAPI:
    """Build the FastAPI app for the sandbox-runtime service."""
    runtime = runtime or DockerContainerRuntime()
    executor = executor or ContainerExecutor()
    invoker = invoker or WorkflowSandboxInvoker(executor)
    handles: dict[str, ContainerHandle] = {}

    app = FastAPI(
        title="Orcheo Sandbox Runtime",
        description=(
            "Internal service that brokers Docker/gVisor sandbox operations. "
            "Mounts the container-runtime socket; never expose publicly."
        ),
    )
    _register_container_routes(app, runtime, handles)
    _register_sandbox_routes(app, executor, invoker)
    _register_credential_relay_route(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:  # pragma: no cover - CLI entrypoint
    """Run the sandbox-runtime service with uvicorn."""
    import uvicorn

    host = os.getenv("ORCHEO_SANDBOX_RUNTIME_HOST", "0.0.0.0")  # noqa: S104
    port = int(os.getenv("ORCHEO_SANDBOX_RUNTIME_PORT", "9090"))
    uvicorn.run(build_service_app(), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
