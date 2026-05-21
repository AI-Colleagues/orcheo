"""HTTP clients that proxy sandbox operations to the sandbox-runtime service.

The Docker socket is root-equivalent on the host (design §Security
Considerations). The backend and Celery worker therefore never touch it
directly — they call the dedicated ``sandbox-runtime`` container over HTTP,
and that container is the only process with the socket mount.

This module provides three clients:

- ``RemoteContainerRuntime`` implements ``ContainerRuntime`` by POSTing to
  ``/containers``. The :class:`SandboxRuntimeManager` uses it for
  acquire/destroy.
- ``RemoteSandboxExec`` implements ``_SandboxExec`` for
  :class:`SandboxedProcessLauncher`, dispatching a one-shot ``docker exec``
  via ``/sandboxes/{sandbox_id}/exec``.
- ``RemoteSandboxRunner`` satisfies the
  :class:`orcheo.sandbox.workflow.SandboxRunner` protocol so the workflow
  dispatcher can stream a ``WorkflowRunSpec`` into a lease via
  ``/sandboxes/{sandbox_id}/dispatch_workflow``.
"""

from __future__ import annotations
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, cast
import httpx
from orcheo.external_agents.models import ProcessExecutionResult
from orcheo.sandbox.errors import SandboxAcquireError, SandboxLifecycleError
from orcheo.sandbox.models import SandboxLease
from orcheo.sandbox.runtime import ContainerHandle, ContainerSpec
from orcheo.sandbox.workflow import WorkflowRunResult, WorkflowRunSpec


class RemoteRuntimeError(SandboxLifecycleError):
    """Raised when the sandbox-runtime service returns an error response."""


def _spec_to_payload(spec: ContainerSpec) -> dict[str, Any]:
    """Serialize a ``ContainerSpec`` for the runtime-service API."""
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


def _handle_from_payload(payload: Mapping[str, Any]) -> ContainerHandle:
    """Build a ``ContainerHandle`` from a runtime-service response."""
    return ContainerHandle(
        container_id=str(payload["container_id"]),
        image=str(payload["image"]),
        workspace_id=str(payload["workspace_id"]),
        runtime=str(payload["runtime"]),
    )


class RemoteContainerRuntime:
    """Container runtime backed by the sandbox-runtime HTTP service."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the runtime.

        Args:
            base_url: Base URL of the sandbox-runtime service
                (e.g. ``http://sandbox-runtime:9090``).
            client: Optional pre-configured ``httpx.Client``; one is created
                if omitted.
            timeout: Request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            self._client.close()

    def start(self, spec: ContainerSpec) -> ContainerHandle:
        """Provision a container via the runtime service."""
        response = self._client.post(
            f"{self._base_url}/containers",
            json=_spec_to_payload(spec),
        )
        self._raise_for_status(response, action="provision container")
        return _handle_from_payload(response.json())

    def stop(self, handle: ContainerHandle) -> None:
        """Stop and remove the container via the runtime service."""
        response = self._client.delete(
            f"{self._base_url}/containers/{handle.container_id}",
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return
        self._raise_for_status(response, action="stop container")

    def is_running(self, handle: ContainerHandle) -> bool:
        """Return True if the runtime service still reports the container alive."""
        response = self._client.get(
            f"{self._base_url}/containers/{handle.container_id}",
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return False
        self._raise_for_status(response, action="inspect container")
        return bool(response.json().get("running", False))

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, action: str) -> None:
        """Translate non-2xx responses into ``RemoteRuntimeError``."""
        if response.is_success:
            return
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        msg = f"sandbox-runtime {action} failed: {response.status_code} {detail}"
        if response.status_code == httpx.codes.CONFLICT:
            raise SandboxAcquireError(msg)
        raise RemoteRuntimeError(msg)


class RemoteSandboxExec:
    """Sandbox exec backend that proxies ``docker exec`` to the runtime service."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        """Initialize the exec backend.

        Args:
            base_url: Base URL of the sandbox-runtime service.
            client: Optional pre-configured ``httpx.AsyncClient``.
            timeout: Default request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._default_timeout = timeout

    async def aclose(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def exec(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout_seconds: float | int | None,
    ) -> ProcessExecutionResult:
        """Execute ``command`` inside ``sandbox_id`` via the runtime service."""
        payload: dict[str, Any] = {
            "command": list(command),
            "cwd": str(cwd) if cwd is not None else None,
            "env": dict(env) if env is not None else None,
            "timeout_seconds": (
                float(timeout_seconds) if timeout_seconds is not None else None
            ),
        }
        # Add a small wall-clock margin so the proxy can return a timeout
        # result instead of being cut off by the HTTP timeout.
        request_timeout = (
            float(timeout_seconds) + 30.0
            if timeout_seconds is not None
            else self._default_timeout
        )
        response = await self._client.post(
            f"{self._base_url}/sandboxes/{sandbox_id}/exec",
            json=payload,
            timeout=request_timeout,
        )
        if not response.is_success:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            msg = f"sandbox-runtime exec failed: {response.status_code} {detail}"
            raise RemoteRuntimeError(msg)
        data = response.json()
        return ProcessExecutionResult(
            command=list(data.get("command", command)),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            exit_code=data.get("exit_code"),
            timed_out=bool(data.get("timed_out", False)),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
        )


def _workflow_spec_payload(spec: WorkflowRunSpec) -> dict[str, Any]:
    """Serialize a ``WorkflowRunSpec`` for transport."""
    return {
        "run_id": spec.run_id,
        "workspace_id": spec.workspace_id,
        "workflow_definition": dict(spec.workflow_definition),
        "inputs": dict(spec.inputs),
        "node_types": list(spec.node_types),
        "runnable_config": dict(spec.runnable_config),
        "state_config": dict(spec.state_config),
    }


# Default HTTP wait for ``dispatch_workflow``. Set above the agent CLI's own
# timeout (``ExternalAgentNode.timeout_seconds`` defaults to 1800s) so the
# HTTP client doesn't give up *before* the in-sandbox work has a chance to
# either finish or report its own timeout.
DEFAULT_WORKFLOW_DISPATCH_TIMEOUT_SECONDS: float = 1860.0


class RemoteSandboxRunner:
    """Workflow ``SandboxRunner`` that proxies to the runtime service."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_WORKFLOW_DISPATCH_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the runner.

        Args:
            base_url: Base URL of the sandbox-runtime service.
            client: Optional pre-configured ``httpx.AsyncClient``.
            timeout: Default request timeout in seconds. Must exceed the
                longest agent timeout the operator expects to dispatch
                through this runner; otherwise the HTTP wait will time out
                before the in-sandbox workflow can return a result and the
                run will surface as ``failed`` with no useful error.
        """
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._default_timeout = timeout

    async def aclose(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def execute(
        self,
        lease: SandboxLease,
        spec: WorkflowRunSpec,
        broker_token: str,
    ) -> WorkflowRunResult:
        """Send the run to the sandbox identified by ``lease`` and await its result."""
        payload = {
            "spec": _workflow_spec_payload(spec),
            "broker_token": broker_token,
        }
        response = await self._client.post(
            f"{self._base_url}/sandboxes/{lease.sandbox_id}/dispatch_workflow",
            json=payload,
            timeout=self._default_timeout,
        )
        if not response.is_success:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            msg = (
                "sandbox-runtime dispatch_workflow failed: "
                f"{response.status_code} {detail}"
            )
            raise RemoteRuntimeError(msg)
        data = response.json()
        return WorkflowRunResult(
            run_id=str(data.get("run_id", spec.run_id)),
            status=str(data.get("status", "failed")),
            outputs=cast(Mapping[str, Any], dict(data.get("outputs") or {})),
            error=data.get("error"),
        )


def serialize_workflow_run_result(result: WorkflowRunResult) -> dict[str, Any]:
    """Serialize a ``WorkflowRunResult`` for the runtime service to return."""
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    return {
        "run_id": result.run_id,
        "status": result.status,
        "outputs": dict(result.outputs),
        "error": result.error,
    }
