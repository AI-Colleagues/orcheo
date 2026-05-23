"""Internal services for gVisor sandbox control and credential relay.

The Docker socket is root-equivalent on the host (design §Security
Considerations). To keep the socket off the backend and worker containers,
this service runs inside the dedicated ``sandbox-runtime`` container — the
only process that mounts ``/var/run/docker.sock`` — and exposes an HTTP API
that other Orcheo services call.

The Docker-facing runtime app is control-plane only and requires the internal
control token for every operation. The credential-relay app is built
separately and exposes only credential resolution and health; tenant
sandboxes never have a network path to the Docker-facing app.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import secrets
import shlex
import socket
import time
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Final
from urllib.parse import urlparse
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.graph.ingestion import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_SCRIPT_SIZE_LIMIT,
)
from orcheo.sandbox.config import SandboxSettings
from orcheo.sandbox.runtime import (
    ContainerHandle,
    ContainerRuntime,
    ContainerSpec,
    DockerContainerRuntime,
)
from orcheo.sandbox.workflow import WorkflowRunResult, WorkflowRunSpec


logger = logging.getLogger(__name__)


_WORKFLOW_RUNNER_MODULE: Final[str] = "orcheo.sandbox.workflow_runner"
_INGESTION_RUNNER_MODULE: Final[str] = "orcheo.sandbox.ingestion_runner"
CONTROL_HEADER: Final[str] = "X-Orcheo-Sandbox-Control-Token"
_SANDBOX_NETWORK: Final[str] = "sandbox-egress"
_SANDBOX_AGENT_RUNTIME_ROOT: Final[str] = "/scratch/agent-runtimes"


class SandboxProvisionRequest(BaseModel):
    """Constrained request for provisioning a hardened workspace sandbox."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    cpu_limit: str = "1.0"
    memory_limit: str = "2g"
    pid_limit: int = Field(default=256, ge=1)
    scratch_size: str = "1g"

    def to_spec(self, settings: SandboxSettings) -> ContainerSpec:
        """Build an isolation-fixed spec from server-side configuration."""
        uid = _stable_uid(self.workspace_id)
        return ContainerSpec(
            image=settings.image,
            workspace_id=self.workspace_id,
            runtime=settings.container_runtime,
            environment=_sandbox_environment(settings),
            cpu_limit=self.cpu_limit,
            memory_limit=self.memory_limit,
            pid_limit=self.pid_limit,
            scratch_size=self.scratch_size,
            user=f"{uid}:{uid}",
            network_mode=_SANDBOX_NETWORK,
            read_only_root=True,
            cap_drop=("ALL",),
            no_new_privileges=True,
            labels={"orcheo.workspace_id": self.workspace_id},
            dns=tuple(settings.sandbox_dns),
            extra_hosts=_resolve_extra_hosts(settings),
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


class ScriptIngestionPayload(BaseModel):
    """Tenant script source validated inside a one-shot sandbox."""

    source: str
    entrypoint: str | None = None
    max_script_bytes: int = Field(
        default=DEFAULT_SCRIPT_SIZE_LIMIT,
        gt=0,
        le=DEFAULT_SCRIPT_SIZE_LIMIT,
    )
    execution_timeout_seconds: float = Field(
        default=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        gt=0,
        le=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    )


def _stable_uid(workspace_id: str) -> int:
    """Derive a stable non-root uid for one workspace."""
    return 10000 + (sum(ord(character) for character in workspace_id) % 50000)


def _sandbox_environment(settings: SandboxSettings) -> dict[str, str]:
    """Return only the fixed environment permitted in child sandboxes."""
    environment = {
        "ORCHEO_CREDENTIAL_BROKER_URL": settings.credential_broker_url,
        "ORCHEO_AGENT_RUNTIME_ROOT": _SANDBOX_AGENT_RUNTIME_ROOT,
    }
    if settings.egress_proxy_url:
        broker_host = urlparse(settings.credential_broker_url).hostname
        no_proxy = ["localhost", "127.0.0.1"]
        if broker_host:
            no_proxy.append(broker_host)
        environment.update(
            {
                "HTTP_PROXY": settings.egress_proxy_url,
                "HTTPS_PROXY": settings.egress_proxy_url,
                "NO_PROXY": ",".join(no_proxy),
            }
        )
    return environment


def _resolve_extra_hosts(settings: SandboxSettings) -> dict[str, str]:
    """Pin the internal relay and proxy hostnames into the child hosts file."""
    hosts: dict[str, str] = {}
    for url in (settings.credential_broker_url, settings.egress_proxy_url):
        if not url:
            continue
        host = urlparse(url).hostname
        if host is None or host in hosts:
            continue
        try:
            socket.inet_pton(socket.AF_INET, host)
            continue
        except OSError:
            pass
        try:
            socket.inet_pton(socket.AF_INET6, host)
            continue
        except OSError:
            pass
        try:
            hosts[host] = socket.gethostbyname(host)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"unable to resolve required sandbox service host {host!r}",
            ) from exc
    return hosts


def _control_dependency(
    expected_token: str,
) -> Callable[[Annotated[str | None, Header(alias=CONTROL_HEADER)]], None]:
    """Create a dependency that rejects unauthenticated control operations."""
    if not expected_token:
        msg = "ORCHEO_SANDBOX_CONTROL_TOKEN must be configured"
        raise RuntimeError(msg)

    def require_control_token(
        supplied_token: Annotated[str | None, Header(alias=CONTROL_HEADER)] = None,
    ) -> None:
        if supplied_token is None or not secrets.compare_digest(
            supplied_token, expected_token
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid sandbox control token",
            )

    return require_control_token


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


class ScriptSandboxInvoker:
    """Execute tenant script ingestion inside a sandbox without credentials."""

    def __init__(self, executor: ContainerExecutor) -> None:
        """Initialize using the Docker-exec adapter."""
        self._executor = executor

    async def invoke(
        self,
        sandbox_id: str,
        payload: ScriptIngestionPayload,
    ) -> dict[str, Any]:
        """Run the ingestion module and return its existing graph payload."""
        encoded = json.dumps(payload.model_dump(), separators=(",", ":"))
        command = [
            "sh",
            "-c",
            (
                "printf '%s\\n' "
                + shlex.quote(encoded)
                + " | python -m "
                + _INGESTION_RUNNER_MODULE
            ),
        ]
        result = await self._executor.exec(
            sandbox_id,
            ExecRequest(
                command=command,
                cwd=None,
                env=None,
                timeout_seconds=payload.execution_timeout_seconds,
            ),
        )
        if result.timed_out:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="script ingestion timed out inside sandbox",
            )
        output = _last_json_line(result.stdout)
        if result.exit_code not in (0, None) or output is None:
            detail = result.stderr.strip() or "ingestion runner produced no output"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=detail,
            )
        parsed = json.loads(output)
        if parsed.get("status") != "succeeded":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(parsed.get("error") or "script ingestion failed"),
            )
        return dict(parsed["payload"])


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
    settings: SandboxSettings,
    authorize: Callable[..., None],
) -> None:
    """Register the container-lifecycle routes on ``app``."""

    @app.post(
        "/internal/containers",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(authorize)],
    )
    def provision(payload: SandboxProvisionRequest) -> dict[str, str]:
        spec = payload.to_spec(settings)
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

    @app.delete(
        "/internal/containers/{container_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(authorize)],
    )
    def stop(container_id: str) -> None:
        handle = handles.pop(container_id, None) or ContainerHandle(
            container_id=container_id,
            image="",
            workspace_id="",
            runtime="",
        )
        if not runtime.is_running(handle):
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

    @app.get(
        "/internal/containers/{container_id}",
        dependencies=[Depends(authorize)],
    )
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
    ingestion_invoker: ScriptSandboxInvoker,
    authorize: Callable[..., None],
) -> None:
    """Register the in-sandbox exec / workflow routes on ``app``."""

    @app.post(
        "/internal/sandboxes/{sandbox_id}/exec",
        dependencies=[Depends(authorize)],
    )
    async def exec_in_sandbox(sandbox_id: str, payload: ExecRequest) -> dict[str, Any]:
        result = await executor.exec(sandbox_id, payload)
        return result.model_dump()

    @app.post(
        "/internal/sandboxes/{sandbox_id}/dispatch_workflow",
        dependencies=[Depends(authorize)],
    )
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

    @app.post(
        "/internal/sandboxes/{sandbox_id}/ingest",
        dependencies=[Depends(authorize)],
    )
    async def ingest_script(
        sandbox_id: str, payload: ScriptIngestionPayload
    ) -> dict[str, Any]:
        return await ingestion_invoker.invoke(sandbox_id, payload)


def _register_credential_relay_route(app: FastAPI) -> None:
    """Relay credential requests from sandboxes to the backend broker."""

    @app.post("/credentials/resolve")
    async def relay_credential_request(
        payload: dict[str, Any],
        authorization: Annotated[str | None, Header()] = None,
        x_orcheo_workspace: Annotated[str | None, Header()] = None,
    ) -> Response:
        broker_url = os.getenv(
            "ORCHEO_CREDENTIAL_BROKER_FORWARD_URL",
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
    ingestion_invoker: ScriptSandboxInvoker | None = None,
    settings: SandboxSettings | None = None,
    control_token: str | None = None,
) -> FastAPI:
    """Build the FastAPI app for the sandbox-runtime service."""
    runtime = runtime or DockerContainerRuntime()
    executor = executor or ContainerExecutor()
    invoker = invoker or WorkflowSandboxInvoker(executor)
    ingestion_invoker = ingestion_invoker or ScriptSandboxInvoker(executor)
    settings = settings or SandboxSettings.from_env()
    authorize = _control_dependency(
        control_token
        if control_token is not None
        else os.getenv("ORCHEO_SANDBOX_CONTROL_TOKEN", "")
    )
    handles: dict[str, ContainerHandle] = {}

    app = FastAPI(
        title="Orcheo Sandbox Runtime",
        description=(
            "Internal service that brokers Docker/gVisor sandbox operations. "
            "Mounts the container-runtime socket; never expose publicly."
        ),
    )
    _register_container_routes(app, runtime, handles, settings, authorize)
    _register_sandbox_routes(
        app,
        executor,
        invoker,
        ingestion_invoker,
        authorize,
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def build_credential_relay_app() -> FastAPI:
    """Build the minimal child-reachable credential relay application."""
    app = FastAPI(
        title="Orcheo Credential Relay",
        description="Minimal sandbox-facing relay for run-scoped credentials.",
    )
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
