"""Tests for the container-runtime abstraction."""

from __future__ import annotations
import pytest
from orcheo.sandbox.runtime import (
    ContainerSpec,
    DockerContainerRuntime,
    InMemoryContainerRuntime,
    render_command,
)


def test_in_memory_runtime_tracks_start_stop() -> None:
    """The in-memory runtime records starts/stops and tracks running state."""
    runtime = InMemoryContainerRuntime()
    spec = ContainerSpec(image="img", workspace_id="W")
    handle = runtime.start(spec)
    assert runtime.is_running(handle)
    assert runtime.started[0][0] is handle
    runtime.stop(handle)
    assert not runtime.is_running(handle)
    assert runtime.stopped[0] is handle


def test_render_command_quotes_arguments() -> None:
    """render_command produces POSIX-quoted output."""
    assert render_command(["echo", "hello world"]) == "echo 'hello world'"


class _FakeContainer:
    """Stand-in for a docker SDK ``Container`` returned by ``containers.run``."""

    def __init__(self, container_id: str) -> None:
        self.id = container_id
        self.status = "running"
        self.killed = False
        self.removed = False

    def kill(self) -> None:
        """Record a kill call."""
        self.killed = True

    def remove(self, force: bool = False) -> None:
        """Record a remove call."""
        del force
        self.removed = True


class _FakeContainers:
    """Stand-in for ``client.containers``."""

    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []
        self._containers: dict[str, _FakeContainer] = {}

    def run(self, **kwargs: object) -> _FakeContainer:
        """Record the run kwargs and return a fake container."""
        container_id = f"c{len(self.runs)}"
        container = _FakeContainer(container_id)
        self._containers[container_id] = container
        self.runs.append({"id": container_id, **kwargs})
        return container

    def get(self, container_id: str) -> _FakeContainer:
        """Look up a previously-run container."""
        return self._containers[container_id]


class _FakeImage:
    """Stand-in for a docker SDK ``Image``."""

    def __init__(self, tag: str) -> None:
        self.tags = [tag]


class _FakeImages:
    """Stand-in for ``client.images``.

    Pretends every requested image is locally available unless the test
    explicitly removes it via :meth:`forget`.
    """

    def __init__(self) -> None:
        self._missing: set[str] = set()

    def get(self, image: str) -> _FakeImage:
        """Return a fake image or raise to mimic the SDK's ``ImageNotFound``."""
        if image in self._missing:
            msg = f"404 Client Error for image: No such image: {image}"
            raise RuntimeError(msg)
        return _FakeImage(image)

    def forget(self, image: str) -> None:
        """Mark ``image`` as missing for the next ``get`` call."""
        self._missing.add(image)


class _FakeClient:
    """Stand-in for ``docker.DockerClient``."""

    def __init__(self) -> None:
        self.containers = _FakeContainers()
        self.images = _FakeImages()


def test_docker_runtime_start_passes_through_security_flags() -> None:
    """start() forwards cgroup, security, and runtime flags to the docker client."""
    client = _FakeClient()
    runtime = DockerContainerRuntime(client=client)
    spec = ContainerSpec(
        image="orcheo/workflow-sandbox:latest",
        workspace_id="W",
        runtime="runsc",
        command=("python", "-m", "orcheo.workflow_runner"),
        cpu_limit="1.5",
        memory_limit="256m",
        pid_limit=128,
        scratch_size="500m",
    )
    handle = runtime.start(spec)
    assert handle.workspace_id == "W"
    assert handle.runtime == "runsc"
    call = client.containers.runs[0]
    assert call["runtime"] == "runsc"
    assert call["user"] == "10001:10001"
    assert call["read_only"] is True
    assert call["cap_drop"] == ["ALL"]
    assert call["security_opt"] == ["no-new-privileges:true"]
    assert call["network_mode"] == "sandbox-egress"
    assert call["pids_limit"] == 128
    assert call["cpu_quota"] == 150_000
    assert call["tmpfs"] == {
        "/scratch": "size=500m,mode=1777,exec",
        "/workspace": "size=500m,mode=1777,exec",
        "/home/orcheo": "size=500m,mode=1777,exec",
    }


def test_docker_runtime_start_raises_actionable_error_when_image_missing() -> None:
    """start() must point operators at ``make docker-build`` when the image
    isn't on the daemon, instead of letting the docker SDK silently attempt a
    registry pull and surface ``pull access denied``."""
    client = _FakeClient()
    client.images.forget("orcheo/workspace-sandbox:latest")
    runtime = DockerContainerRuntime(client=client)
    spec = ContainerSpec(
        image="orcheo/workspace-sandbox:latest",
        workspace_id="W",
    )
    with pytest.raises(RuntimeError) as excinfo:
        runtime.start(spec)
    message = str(excinfo.value)
    assert "make docker-build" in message
    assert "docker compose build workspace-sandbox" in message
    # The container.run call must not have fired — we fail fast before the
    # implicit registry pull.
    assert client.containers.runs == []


def test_docker_runtime_stop_kills_and_removes_container() -> None:
    """stop() kills then removes the container even if kill raises."""
    client = _FakeClient()
    runtime = DockerContainerRuntime(client=client)
    handle = runtime.start(
        ContainerSpec(image="img", workspace_id="W", command=("/bin/true",))
    )
    runtime.stop(handle)
    container = client.containers.get(handle.container_id)
    assert container.killed
    assert container.removed


def test_docker_runtime_is_running_reflects_status() -> None:
    """is_running() returns True only when the docker status is 'running'."""
    client = _FakeClient()
    runtime = DockerContainerRuntime(client=client)
    handle = runtime.start(ContainerSpec(image="img", workspace_id="W"))
    assert runtime.is_running(handle)
    client.containers.get(handle.container_id).status = "exited"
    assert not runtime.is_running(handle)


def test_container_handle_as_dict_returns_all_fields() -> None:
    """as_dict() serialises every ContainerHandle field."""
    from orcheo.sandbox.runtime import ContainerHandle

    handle = ContainerHandle(
        container_id="abc123",
        image="orcheo/sandbox:latest",
        workspace_id="ws-1",
        runtime="runsc",
    )
    result = handle.as_dict()
    assert result == {
        "container_id": "abc123",
        "image": "orcheo/sandbox:latest",
        "workspace_id": "ws-1",
        "runtime": "runsc",
    }


def test_docker_runtime_ensure_client_uses_injected_client() -> None:
    """_ensure_client() returns the pre-injected client immediately."""
    client = _FakeClient()
    runtime = DockerContainerRuntime(client=client)
    assert runtime._ensure_client() is client


def test_docker_runtime_ensure_client_imports_docker_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_client() calls docker.from_env() when no client was injected."""
    import sys
    import types

    fake_client = object()
    fake_docker = types.ModuleType("docker")
    fake_docker.from_env = lambda: fake_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    runtime = DockerContainerRuntime()
    result = runtime._ensure_client()
    assert result is fake_client
    # Second call should return the cached client without re-importing.
    assert runtime._ensure_client() is fake_client


def test_docker_runtime_no_new_privileges_false_omits_security_opt() -> None:
    """When no_new_privileges=False, security_opt is empty."""
    from orcheo.sandbox.runtime import DockerContainerRuntime

    client = _FakeClient()
    runtime = DockerContainerRuntime(client=client)
    spec = ContainerSpec(
        image="img",
        workspace_id="W",
        no_new_privileges=False,
    )
    handle = runtime.start(spec)
    call = client.containers.runs[0]
    assert handle is not None
    assert call["security_opt"] == []


class _UnknownRuntimeContainers(_FakeContainers):
    """Containers facade that mimics Docker's 'unknown runtime' 400 error."""

    def run(self, **kwargs: object) -> _FakeContainer:  # type: ignore[override]
        raise RuntimeError(
            "docker.errors.APIError: 400 Client Error: "
            "unknown or invalid runtime name: runsc"
        )


class _ClientWithRuntimes(_FakeClient):
    """Fake client that also exposes ``info()`` so the helper can probe runtimes."""

    def __init__(self, available: list[str] | None = None) -> None:
        super().__init__()
        self.containers = _UnknownRuntimeContainers()
        self._available = available or ["runc", "io.containerd.runc.v2"]

    def info(self) -> dict[str, object]:
        return {"Runtimes": {name: {"path": name} for name in self._available}}


def test_docker_runtime_translates_unknown_runtime_error() -> None:
    """An 'unknown runtime' Docker error is rewritten with available runtimes
    and a hint pointing at ORCHEO_CONTAINER_RUNTIME."""
    import pytest

    client = _ClientWithRuntimes(available=["runc"])
    runtime = DockerContainerRuntime(client=client)
    spec = ContainerSpec(image="img", workspace_id="W", runtime="runsc")

    with pytest.raises(RuntimeError) as excinfo:
        runtime.start(spec)

    message = str(excinfo.value)
    assert "'runsc'" in message
    assert "runc" in message  # lists available runtimes
    assert "ORCHEO_CONTAINER_RUNTIME" in message


def test_docker_runtime_other_errors_are_left_untouched() -> None:
    """Non-runtime errors are propagated unchanged so debugging is unaffected."""
    import pytest

    class _BoomContainers(_FakeContainers):
        def run(self, **kwargs: object) -> _FakeContainer:  # type: ignore[override]
            raise RuntimeError("some unrelated docker failure")

    client = _FakeClient()
    client.containers = _BoomContainers()
    runtime = DockerContainerRuntime(client=client)

    with pytest.raises(RuntimeError, match="some unrelated docker failure"):
        runtime.start(ContainerSpec(image="img", workspace_id="W"))


def test_docker_runtime_translates_runtime_error_without_info() -> None:
    """If ``client.info()`` itself fails the helper still surfaces a clean message."""
    import pytest

    class _NoInfoClient(_ClientWithRuntimes):
        def info(self) -> dict[str, object]:  # type: ignore[override]
            raise RuntimeError("info unavailable")

    runtime = DockerContainerRuntime(client=_NoInfoClient())
    spec = ContainerSpec(image="img", workspace_id="W", runtime="runsc")

    with pytest.raises(RuntimeError) as excinfo:
        runtime.start(spec)

    message = str(excinfo.value)
    assert "'runsc'" in message
    assert "ORCHEO_CONTAINER_RUNTIME" in message
