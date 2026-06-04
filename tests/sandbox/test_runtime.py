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
        self.exec_calls: list[dict[str, object]] = []

    def kill(self) -> None:
        """Record a kill call."""
        self.killed = True

    def remove(self, force: bool = False) -> None:
        """Record a remove call."""
        del force
        self.removed = True

    def exec_run(self, cmd: list[str], user: str | None = None) -> object:
        """Record an exec invocation and report success."""
        self.exec_calls.append({"cmd": cmd, "user": user})
        return type("ExecResult", (), {"exit_code": 0, "output": b""})()


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
        self._pullable: set[str] = set()
        self.pulled: list[str] = []

    def get(self, image: str) -> _FakeImage:
        """Return a fake image or raise to mimic the SDK's ``ImageNotFound``."""
        if image in self._missing:
            msg = f"404 Client Error for image: No such image: {image}"
            raise RuntimeError(msg)
        return _FakeImage(image)

    def pull(self, image: str) -> _FakeImage:
        """Mimic ``client.images.pull`` for registry-hosted images."""
        self.pulled.append(image)
        if image in self._pullable:
            self._missing.discard(image)
            return _FakeImage(image)
        msg = f"pull access denied for {image}"
        raise RuntimeError(msg)

    def forget(self, image: str, *, pullable: bool = False) -> None:
        """Mark ``image`` as missing locally; ``pullable`` allows a pull."""
        self._missing.add(image)
        if pullable:
            self._pullable.add(image)


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
        "/tmp": "size=500m,mode=1777,exec",
    }


def test_docker_runtime_forwards_volumes() -> None:
    """Named volumes are forwarded to docker so sandbox state can persist."""
    client = _FakeClient()
    runtime = DockerContainerRuntime(client=client)
    spec = ContainerSpec(
        image="img",
        workspace_id="W",
        volumes={
            "sandbox_scratch": {
                "bind": "/state",
                "mode": "rw",
            }
        },
    )
    runtime.start(spec)
    call = client.containers.runs[0]
    assert call["volumes"] == {"sandbox_scratch": {"bind": "/state", "mode": "rw"}}
    container = client.containers.get("c0")
    assert container.exec_calls == [
        {
            "cmd": [
                "python",
                "-c",
                "import os\n"
                "path = '/state'\n"
                "uid = 10001\n"
                "gid = 10001\n"
                "os.chown(path, uid, gid)\n"
                "for root, dirs, files in os.walk(path):\n"
                "    os.chown(root, uid, gid)\n"
                "    for name in dirs:\n"
                "        os.chown(os.path.join(root, name), uid, gid)\n"
                "    for name in files:\n"
                "        os.chown(os.path.join(root, name), uid, gid)\n",
            ],
            "user": "0:0",
        }
    ]


def test_docker_runtime_forwards_dns_and_extra_hosts() -> None:
    """When set, dns and extra_hosts make it into the docker run call.

    gVisor sandboxes cannot reach Docker's embedded DNS, so the manager passes
    an upstream resolver list and a static /etc/hosts mapping for the broker
    hop — both have to land on the container.
    """
    client = _FakeClient()
    runtime = DockerContainerRuntime(client=client)
    spec = ContainerSpec(
        image="img",
        workspace_id="W",
        dns=("1.1.1.1", "8.8.8.8"),
        extra_hosts={"sandbox-runtime": "10.0.0.7"},
    )
    runtime.start(spec)
    call = client.containers.runs[0]
    assert call["dns"] == ["1.1.1.1", "8.8.8.8"]
    assert call["extra_hosts"] == {"sandbox-runtime": "10.0.0.7"}


def test_docker_runtime_omits_dns_and_extra_hosts_when_unset() -> None:
    """Empty dns/extra_hosts must not show up in the docker run call.

    The default Docker resolver behaviour is correct for the manager's plain
    Compose containers; only sandbox children need the override.
    """
    client = _FakeClient()
    runtime = DockerContainerRuntime(client=client)
    runtime.start(ContainerSpec(image="img", workspace_id="W"))
    call = client.containers.runs[0]
    assert "dns" not in call
    assert "extra_hosts" not in call


def test_docker_runtime_start_raises_actionable_error_when_pull_fails() -> None:
    """start() must surface an actionable error when the image is absent and
    can't be pulled, naming both the registry and the local-build fallback."""
    client = _FakeClient()
    client.images.forget("ghcr.io/ai-colleagues/orcheo-workspace-sandbox:latest")
    runtime = DockerContainerRuntime(client=client)
    spec = ContainerSpec(
        image="ghcr.io/ai-colleagues/orcheo-workspace-sandbox:latest",
        workspace_id="W",
    )
    with pytest.raises(RuntimeError) as excinfo:
        runtime.start(spec)
    message = str(excinfo.value)
    assert "orcheo-workspace-sandbox" in message
    assert "make docker-build" in message
    # The pull was attempted, but the container.run call must not have fired.
    assert client.images.pulled == [
        "ghcr.io/ai-colleagues/orcheo-workspace-sandbox:latest"
    ]
    assert client.containers.runs == []


def test_docker_runtime_start_pulls_missing_image_then_runs() -> None:
    """start() pulls a missing-but-published image and proceeds to run it."""
    client = _FakeClient()
    image = "ghcr.io/ai-colleagues/orcheo-workspace-sandbox:latest"
    client.images.forget(image, pullable=True)
    runtime = DockerContainerRuntime(client=client)
    handle = runtime.start(ContainerSpec(image=image, workspace_id="W"))
    assert client.images.pulled == [image]
    assert len(client.containers.runs) == 1
    assert handle.image == image


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


def test_ensure_image_present_passes_through_non_notfound_errors() -> None:
    """_ensure_image_present returns (does not raise, and does not pull) for
    non-'not found' image errors so daemon-connectivity issues bubble up via
    start()'s outer handler."""

    class _FakeImages:
        def get(self, image: str) -> object:
            # A non-"not found" error (e.g. daemon connectivity issue).
            raise RuntimeError("connection refused to docker daemon")

        def pull(self, image: str) -> object:
            raise AssertionError("pull must not be attempted on daemon errors")

    class _FakeClientConnErr:
        def __init__(self) -> None:
            self.images = _FakeImages()

    # The method should return (not raise) for non-missing-image errors.
    DockerContainerRuntime._ensure_image_present(
        _FakeClientConnErr(), "orcheo/workspace-sandbox:latest"
    )
    # If we reach here without an exception the branch is covered.


def test_docker_runtime_stop_remove_called_even_if_kill_raises() -> None:
    """stop() calls remove in the finally block even when kill() raises (branch 221->225)."""

    class _KillBoomContainer(_FakeContainer):
        def kill(self) -> None:
            raise RuntimeError("kill blocked by OOM killer")

    class _KillBoomContainers(_FakeContainers):
        def run(self, **kwargs: object) -> _KillBoomContainer:
            container_id = f"c{len(self.runs)}"
            container = _KillBoomContainer(container_id)
            self._containers[container_id] = container
            self.runs.append({"id": container_id, **kwargs})
            return container

    client = _FakeClient()
    client.containers = _KillBoomContainers()
    runtime = DockerContainerRuntime(client=client)
    handle = runtime.start(ContainerSpec(image="img", workspace_id="W"))
    container = client.containers.get(handle.container_id)

    with pytest.raises(RuntimeError, match="kill blocked"):
        runtime.stop(handle)

    # remove must have been called in the finally block despite kill raising.
    assert container.removed is True


def test_docker_runtime_translates_runtime_error_with_empty_runtimes_info() -> None:
    """Branch 221->225: when info() Runtimes is not a dict, available stays empty."""
    import pytest

    class _NoRuntimesClient(_ClientWithRuntimes):
        def info(self) -> dict[str, object]:
            # info() returns a dict without 'Runtimes', so runtimes is None.
            return {}

    runtime = DockerContainerRuntime(client=_NoRuntimesClient())
    spec = ContainerSpec(image="img", workspace_id="W", runtime="runsc")

    with pytest.raises(RuntimeError) as excinfo:
        runtime.start(spec)

    message = str(excinfo.value)
    assert "'runsc'" in message
    assert "ORCHEO_CONTAINER_RUNTIME" in message
    # No available runtimes listed since info had none.
    assert "Available runtimes" not in message
