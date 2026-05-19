"""Tests for the container-runtime abstraction."""

from __future__ import annotations
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


class _FakeClient:
    """Stand-in for ``docker.DockerClient``."""

    def __init__(self) -> None:
        self.containers = _FakeContainers()


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
    assert call["tmpfs"] == {"/scratch": "size=500m,mode=1777"}


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
