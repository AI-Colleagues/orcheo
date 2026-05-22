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
        self._require_local_image(client, spec.image)
        host_config = self._build_host_config(spec)
        cmd = list(spec.command) if spec.command else None
        try:
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
        except Exception as exc:
            self._reraise_with_helpful_runtime_error(exc, spec, client)
            raise
        return ContainerHandle(
            container_id=container.id,
            image=spec.image,
            workspace_id=spec.workspace_id,
            runtime=spec.runtime,
        )

    @staticmethod
    def _require_local_image(client: object, image: str) -> None:
        """Fail fast if ``image`` isn't built locally.

        ``client.containers.run`` quietly attempts a registry pull when the
        image is missing — but ``orcheo/workspace-sandbox`` is a local-only
        image (built via ``make docker-build``), so the implicit pull always
        fails with a confusing "pull access denied" message. Catching the
        absence here turns that into an actionable error pointing the
        operator at the build target.
        """
        try:
            client.images.get(image)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — translate any docker SDK error
            message = str(exc).lower()
            if "not found" not in message and "no such image" not in message:
                # Not a missing-image error (could be a daemon-connectivity
                # problem). Let the original exception bubble; ``start()``'s
                # outer try/except already routes it through the daemon-runtime
                # diagnostic helper.
                return
            msg = (
                f"Workspace sandbox image {image!r} is not present on the "
                "Docker daemon. The image is local-only and cannot be pulled "
                "from a registry. Run `make docker-build` (or "
                "`docker compose build workspace-sandbox`) and try again."
            )
            raise RuntimeError(msg) from exc

    @staticmethod
    def _reraise_with_helpful_runtime_error(
        exc: Exception, spec: ContainerSpec, client: object
    ) -> None:
        """Translate Docker's cryptic 'unknown runtime' error.

        The Docker daemon returns ``400 Bad Request ("unknown or invalid
        runtime name: <name>")`` when the requested runtime is not registered.
        That message doesn't tell the operator what their options are. We
        intercept it, list the runtimes the daemon actually has, and explain
        how to fix the configuration. Any other exception is left untouched
        so the original stack trace propagates.
        """
        message = str(exc)
        if "unknown or invalid runtime name" not in message:
            return
        available: list[str] = []
        try:
            info = client.info()  # type: ignore[attr-defined]
            runtimes = info.get("Runtimes") if isinstance(info, dict) else None
            if isinstance(runtimes, dict):
                available = sorted(runtimes.keys())
        except Exception:  # pragma: no cover - best-effort probe
            available = []
        hint = (
            f"Docker daemon does not have the container runtime "
            f"{spec.runtime!r} registered. "
        )
        if available:
            hint += f"Available runtimes on this host: {', '.join(available)}. "
        hint += (
            "Either install/register the runtime (e.g. install gVisor and add "
            "it to /etc/docker/daemon.json for `runsc`), or set the env var "
            "ORCHEO_CONTAINER_RUNTIME to one of the available names "
            "(typically `runc` for local development)."
        )
        raise RuntimeError(hint) from exc

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
        """Translate ``spec`` resource limits into Docker host-config kwargs.

        ``/scratch``, ``/workspace``, ``/home/orcheo``, and ``/tmp`` are
        mounted as tmpfs with mode 1777 so that the per-workspace UID can
        write into them despite the read-only rootfs:

        - ``/scratch`` hosts the managed external-agent runtime tree (see
          ``ORCHEO_AGENT_RUNTIME_ROOT`` in the manager).
        - ``/workspace`` is the agent's working directory tree
          (``DEFAULT_WORKSPACE_AGENT_ROOT``).
        - ``/home/orcheo`` is the container's ``HOME`` (baked in the image);
          ``npm``, ``git``, and provider CLIs all expect a writable home for
          caches and config (``~/.npm``, ``~/.config``, ``~/.cache``, etc.).
        - ``/tmp`` is required by the Claude Code native binary, which
          writes scratch files there during ``--print`` runs and hangs
          silently (after policy-limits init, before any API request) when
          the path is on a read-only rootfs.

        The home path is intentionally hardcoded to match
        ``Dockerfile.workspace-sandbox``; both must change together if the
        image's HOME ever moves.

        ``exec`` is explicitly enabled on every mount because Docker's
        default tmpfs options are ``rw,nosuid,nodev,noexec`` — and the
        managed provider CLIs (``/scratch/agent-runtimes/<provider>/bin/...``)
        plus git hooks and any helper scripts the agent drops in
        ``/workspace`` need to be runnable from these paths. ``nosuid`` and
        ``nodev`` are kept (we don't want setuid binaries or device nodes in
        a sandbox); only ``noexec`` is dropped.
        """
        cpu_quota = int(float(spec.cpu_limit) * 100_000)
        tmpfs_options = f"size={spec.scratch_size},mode=1777,exec"
        return {
            "mem_limit": spec.memory_limit,
            "pids_limit": spec.pid_limit,
            "cpu_period": 100_000,
            "cpu_quota": cpu_quota,
            "tmpfs": {
                "/scratch": tmpfs_options,
                "/workspace": tmpfs_options,
                "/home/orcheo": tmpfs_options,
                "/tmp": tmpfs_options,
            },
        }


def render_command(command: list[str] | tuple[str, ...]) -> str:
    """Render a command list as a shell-escaped string for logging.

    Args:
        command: Argv to render.

    Returns:
        A POSIX-quoted command string.
    """
    return shlex.join(command)
