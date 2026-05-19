"""Container-runtime abstraction backing the Sandbox Runtime Manager.

The Sandbox Runtime Manager talks to a ``ContainerRuntime`` protocol so the
underlying engine (gVisor via Docker/containerd, plain Docker, or an in-memory
fake for tests) is swappable. Real deployments inject the Docker-backed
implementation; tests use ``InMemoryContainerRuntime``.

The Docker-backed runtime is intentionally constructed lazily — its dependency
on the ``docker`` SDK is imported only when the runtime is actually started, so
unit tests and offline environments don't need the SDK installed.
"""

from __future__ import annotations
import shlex
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ContainerSpec:
    """Declarative description of a sandbox container to spawn."""

    image: str
    workspace_id: str
    runtime: str = "runsc"
    command: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    cpu_limit: str = "1.0"
    memory_limit: str = "512m"
    pid_limit: int = 256
    scratch_size: str = "1g"
    user: str = "10001:10001"
    network_mode: str = "sandbox-egress"
    read_only_root: bool = True
    cap_drop: tuple[str, ...] = ("ALL",)
    no_new_privileges: bool = True
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass
class ContainerHandle:
    """Reference to a running sandbox container."""

    container_id: str
    image: str
    workspace_id: str
    runtime: str

    def as_dict(self) -> dict[str, str]:
        """Return a plain dict suitable for serializing in audit events."""
        return {
            "container_id": self.container_id,
            "image": self.image,
            "workspace_id": self.workspace_id,
            "runtime": self.runtime,
        }


class ContainerRuntime(Protocol):
    """Protocol for engines that can run isolated sandbox containers."""

    def start(self, spec: ContainerSpec) -> ContainerHandle:
        """Provision and start a sandbox container from ``spec``."""

    def stop(self, handle: ContainerHandle) -> None:
        """Stop and remove the sandbox container identified by ``handle``."""

    def is_running(self, handle: ContainerHandle) -> bool:
        """Return True if the sandbox container is still alive."""


class InMemoryContainerRuntime:
    """Test/dev container runtime that records starts/stops without Docker."""

    def __init__(self) -> None:
        """Initialize an empty in-memory runtime."""
        self.started: list[tuple[ContainerHandle, ContainerSpec]] = []
        self.stopped: list[ContainerHandle] = []
        self._running: set[str] = set()

    def start(self, spec: ContainerSpec) -> ContainerHandle:
        """Record a start and return a synthetic handle."""
        handle = ContainerHandle(
            container_id=f"in-memory-{uuid.uuid4().hex[:12]}",
            image=spec.image,
            workspace_id=spec.workspace_id,
            runtime=spec.runtime,
        )
        self._running.add(handle.container_id)
        self.started.append((handle, spec))
        return handle

    def stop(self, handle: ContainerHandle) -> None:
        """Record a stop and mark the container as not running."""
        self._running.discard(handle.container_id)
        self.stopped.append(handle)

    def is_running(self, handle: ContainerHandle) -> bool:
        """Return True if the synthetic container is still considered running."""
        return handle.container_id in self._running


class DockerContainerRuntime:
    """Docker-backed ``ContainerRuntime``.

    Imports the ``docker`` SDK lazily so unit tests don't require it. The
    underlying client is expected to be configured by the operator (Docker
    socket mount or rootless setup) per the design document's note on
    container-runtime socket safety.
    """

    def __init__(self, client: object | None = None) -> None:
        """Initialize the runtime.

        Args:
            client: Optional pre-configured Docker client. When omitted, the
                ``docker`` SDK is imported and ``docker.from_env()`` is used.
        """
        self._client = client

    def _ensure_client(self) -> object:
        """Lazily import the docker SDK and construct a default client."""
        if self._client is not None:
            return self._client
        try:
            import docker  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - environment-dependent
            msg = (
                "The 'docker' SDK is required for DockerContainerRuntime. "
                "Install it or inject a custom client."
            )
            raise RuntimeError(msg) from exc
        self._client = docker.from_env()
        return self._client

    def start(self, spec: ContainerSpec) -> ContainerHandle:
        """Spawn a Docker container that satisfies ``spec``."""
        client = self._ensure_client()
        host_config = self._build_host_config(spec)
        cmd = list(spec.command) if spec.command else None
        container = client.containers.run(  # type: ignore[attr-defined]
            image=spec.image,
            command=cmd,
            detach=True,
            runtime=spec.runtime,
            environment=dict(spec.environment),
            user=spec.user,
            read_only=spec.read_only_root,
            cap_drop=list(spec.cap_drop),
            security_opt=self._build_security_opt(spec),
            network_mode=spec.network_mode,
            labels={
                "orcheo.workspace_id": spec.workspace_id,
                **dict(spec.labels),
            },
            **host_config,
        )
        return ContainerHandle(
            container_id=container.id,
            image=spec.image,
            workspace_id=spec.workspace_id,
            runtime=spec.runtime,
        )

    def stop(self, handle: ContainerHandle) -> None:
        """Stop and remove the container behind ``handle``."""
        client = self._ensure_client()
        container = client.containers.get(handle.container_id)  # type: ignore[attr-defined]
        try:
            container.kill()
        finally:
            container.remove(force=True)

    def is_running(self, handle: ContainerHandle) -> bool:
        """Return True if the Docker container is still in the running state."""
        client = self._ensure_client()
        try:
            container = client.containers.get(handle.container_id)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - SDK-specific
            return False
        return getattr(container, "status", "") == "running"

    @staticmethod
    def _build_security_opt(spec: ContainerSpec) -> list[str]:
        """Translate ``spec`` flags into Docker ``security_opt`` entries."""
        opts: list[str] = []
        if spec.no_new_privileges:
            opts.append("no-new-privileges:true")
        return opts

    @staticmethod
    def _build_host_config(spec: ContainerSpec) -> dict[str, object]:
        """Translate ``spec`` resource limits into Docker host-config kwargs."""
        cpu_quota = int(float(spec.cpu_limit) * 100_000)
        return {
            "mem_limit": spec.memory_limit,
            "pids_limit": spec.pid_limit,
            "cpu_period": 100_000,
            "cpu_quota": cpu_quota,
            "tmpfs": {"/scratch": f"size={spec.scratch_size},mode=1777"},
        }


def render_command(command: list[str] | tuple[str, ...]) -> str:
    """Render a command list as a shell-escaped string for logging.

    Args:
        command: Argv to render.

    Returns:
        A POSIX-quoted command string.
    """
    return shlex.join(command)
