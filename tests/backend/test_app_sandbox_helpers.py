"""Tests for the backend-shared sandbox bootstrap helpers."""

from __future__ import annotations
from types import SimpleNamespace
from typing import Any
import pytest
from orcheo_backend.app import sandbox as sandbox_module
from orcheo_backend.app.sandbox import (
    SandboxRuntimeNotConfiguredError,
    _SandboxBootstrap,
    build_credential_broker,
    build_workflow_run_spec,
    collect_node_types,
    ensure_sandbox_configured,
    install_sandbox_bootstrap,
    reset_sandbox_bootstrap,
    run_uses_trusted_nodes_only,
    _fast_path_enabled,
)


def test_run_uses_trusted_nodes_only_returns_true_for_trusted_only() -> None:
    assert run_uses_trusted_nodes_only(("AINode", "ChatModelNode"))


def test_run_uses_trusted_nodes_only_returns_false_for_unknown_type() -> None:
    assert not run_uses_trusted_nodes_only(("AINode", "TenantPythonNode"))


def test_run_uses_trusted_nodes_only_fails_closed_on_empty() -> None:
    """A graph with no parseable node types must not take the in-worker fast path."""
    assert not run_uses_trusted_nodes_only(())


def test_collect_node_types_returns_empty_for_non_dict() -> None:
    assert collect_node_types("not a dict") == ()


def test_collect_node_types_extracts_unique_types() -> None:
    config = {
        "nodes": [
            {"type": "AINode"},
            {"kind": "RSSNode"},
            {"type": "AINode"},  # duplicate filtered
            "not-a-dict",
            {"name": "no-type-here"},
        ]
    }
    assert collect_node_types(config) == ("AINode", "RSSNode")


def test_build_workflow_run_spec_carries_node_types() -> None:
    spec = build_workflow_run_spec(
        execution_id="exec-1",
        workspace_id="ws-1",
        graph_config={"nodes": [{"type": "TenantPythonNode"}]},
        inputs={"x": 1},
        runnable_config={"configurable": {"thread_id": "exec-1"}},
        state_config={"configurable": {"ai_model": "openai:test"}},
    )
    assert spec.run_id == "exec-1"
    assert spec.workspace_id == "ws-1"
    assert spec.node_types == ("TenantPythonNode",)
    assert spec.runnable_config == {"configurable": {"thread_id": "exec-1"}}
    assert spec.state_config == {"configurable": {"ai_model": "openai:test"}}


def test_ensure_sandbox_configured_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated calls must not raise and must not rebind an existing broker."""
    calls: list[Any] = []

    class _StubBootstrap:
        def __init__(self) -> None:
            self._broker: Any = None

        def configure(self, broker: Any) -> None:
            calls.append(broker)
            self._broker = broker

    stub = _StubBootstrap()
    monkeypatch.setattr(sandbox_module, "_bootstrap", stub)

    # Provide a fake vault so build_credential_broker doesn't blow up.
    monkeypatch.setattr(sandbox_module, "get_vault", lambda: SimpleNamespace())
    monkeypatch.setenv("ORCHEO_CREDENTIAL_BROKER_SECRET", "abc")

    ensure_sandbox_configured()
    ensure_sandbox_configured()  # idempotent — must not rebind

    assert len(calls) == 1


def test_build_credential_broker_requires_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The broker refuses to build without ORCHEO_CREDENTIAL_BROKER_SECRET."""
    monkeypatch.delenv("ORCHEO_CREDENTIAL_BROKER_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="ORCHEO_CREDENTIAL_BROKER_SECRET"):
        build_credential_broker()


# ---------------------------------------------------------------------------
# _SandboxBootstrap unit tests
# ---------------------------------------------------------------------------


def test_bootstrap_runtime_url_raises_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_runtime_url raises SandboxRuntimeNotConfiguredError when env var is unset."""
    monkeypatch.delenv("ORCHEO_SANDBOX_RUNTIME_URL", raising=False)
    bootstrap = _SandboxBootstrap()
    with pytest.raises(
        SandboxRuntimeNotConfiguredError, match="ORCHEO_SANDBOX_RUNTIME_URL"
    ):
        bootstrap._runtime_url()


def test_bootstrap_runtime_url_returns_env_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_runtime_url returns the configured env var when set."""
    monkeypatch.setenv("ORCHEO_SANDBOX_RUNTIME_URL", "http://sandbox-runtime:9090")
    bootstrap = _SandboxBootstrap()
    assert bootstrap._runtime_url() == "http://sandbox-runtime:9090"


def test_bootstrap_configure_requires_control_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend/worker startup fails before serving without control auth."""
    monkeypatch.delenv("ORCHEO_SANDBOX_CONTROL_TOKEN", raising=False)
    bootstrap = _SandboxBootstrap()
    with pytest.raises(
        SandboxRuntimeNotConfiguredError, match="ORCHEO_SANDBOX_CONTROL_TOKEN"
    ):
        bootstrap.configure(SimpleNamespace())  # type: ignore[arg-type]


def test_bootstrap_ensure_manager_creates_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_manager lazily creates runtime and manager."""
    monkeypatch.setenv("ORCHEO_SANDBOX_RUNTIME_URL", "http://sandbox-runtime:9090")

    created_runtimes: list[Any] = []
    created_managers: list[Any] = []

    class _FakeRuntime:
        def __init__(self, url: str, *, control_token: str) -> None:
            self.url = url
            assert control_token == "test-sandbox-control-token"
            created_runtimes.append(self)

    class _FakeManager:
        def __init__(self, *, runtime: Any, settings: Any) -> None:
            self.runtime = runtime
            created_managers.append(self)

    monkeypatch.setattr(sandbox_module, "RemoteContainerRuntime", _FakeRuntime)
    monkeypatch.setattr(sandbox_module, "SandboxRuntimeManager", _FakeManager)
    monkeypatch.setattr(
        sandbox_module, "SandboxSettings", SimpleNamespace(from_env=lambda: {})
    )

    bootstrap = _SandboxBootstrap()
    manager = bootstrap._ensure_manager()
    assert len(created_managers) == 1
    assert manager is created_managers[0]

    # Second call must return cached manager without creating a new one.
    manager2 = bootstrap._ensure_manager()
    assert manager2 is manager
    assert len(created_managers) == 1


def test_bootstrap_launcher_creates_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """launcher() creates a SandboxedProcessLauncher on first call and caches it."""
    monkeypatch.setenv("ORCHEO_SANDBOX_RUNTIME_URL", "http://sandbox-runtime:9090")

    class _FakeRuntime:
        def __init__(self, url: str, *, control_token: str) -> None:
            assert control_token == "test-sandbox-control-token"
            pass

    class _FakeManager:
        def __init__(self, *, runtime: Any, settings: Any) -> None:
            pass

    class _FakeExec:
        def __init__(self, url: str, *, control_token: str) -> None:
            self.url = url
            assert control_token == "test-sandbox-control-token"

    class _FakeLauncher:
        def __init__(self, manager: Any, *, exec_backend: Any) -> None:
            self.exec_backend = exec_backend

    monkeypatch.setattr(sandbox_module, "RemoteContainerRuntime", _FakeRuntime)
    monkeypatch.setattr(sandbox_module, "SandboxRuntimeManager", _FakeManager)
    monkeypatch.setattr(
        sandbox_module, "SandboxSettings", SimpleNamespace(from_env=lambda: {})
    )
    monkeypatch.setattr(sandbox_module, "RemoteSandboxExec", _FakeExec)
    monkeypatch.setattr(sandbox_module, "SandboxedProcessLauncher", _FakeLauncher)

    bootstrap = _SandboxBootstrap()
    launcher = bootstrap.launcher()
    assert isinstance(launcher, _FakeLauncher)

    # Second call returns the same instance.
    launcher2 = bootstrap.launcher()
    assert launcher2 is launcher


def test_bootstrap_dispatcher_raises_without_broker() -> None:
    """dispatcher() raises SandboxRuntimeNotConfiguredError when broker not set."""
    bootstrap = _SandboxBootstrap()
    with pytest.raises(SandboxRuntimeNotConfiguredError, match="broker"):
        bootstrap.dispatcher()


def test_bootstrap_dispatcher_creates_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dispatcher() creates a WorkflowSandboxDispatcher after configure() is called."""
    monkeypatch.setenv("ORCHEO_SANDBOX_RUNTIME_URL", "http://sandbox-runtime:9090")

    class _FakeRuntime:
        def __init__(self, url: str, *, control_token: str) -> None:
            assert control_token == "test-sandbox-control-token"
            pass

    class _FakeManager:
        def __init__(self, *, runtime: Any, settings: Any) -> None:
            pass

    class _FakeRunner:
        def __init__(self, url: str, *, control_token: str) -> None:
            assert control_token == "test-sandbox-control-token"
            pass

    dispatchers_created: list[Any] = []

    class _FakeDispatcher:
        def __init__(
            self,
            manager: Any,
            runner: Any,
            broker: Any,
            *,
            allow_in_worker_fast_path: bool,
        ) -> None:
            self.broker = broker
            dispatchers_created.append(self)

    monkeypatch.setattr(sandbox_module, "RemoteContainerRuntime", _FakeRuntime)
    monkeypatch.setattr(sandbox_module, "SandboxRuntimeManager", _FakeManager)
    monkeypatch.setattr(
        sandbox_module, "SandboxSettings", SimpleNamespace(from_env=lambda: {})
    )
    monkeypatch.setattr(sandbox_module, "RemoteSandboxRunner", _FakeRunner)
    monkeypatch.setattr(sandbox_module, "WorkflowSandboxDispatcher", _FakeDispatcher)

    broker = SimpleNamespace()
    bootstrap = _SandboxBootstrap()
    bootstrap.configure(broker)
    dispatcher = bootstrap.dispatcher()
    assert isinstance(dispatcher, _FakeDispatcher)
    assert dispatcher.broker is broker

    # Second call returns cached instance.
    dispatcher2 = bootstrap.dispatcher()
    assert dispatcher2 is dispatcher
    assert len(dispatchers_created) == 1


def test_reset_sandbox_bootstrap_replaces_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reset_sandbox_bootstrap() replaces _bootstrap with a fresh instance."""
    original = sandbox_module._bootstrap
    try:
        reset_sandbox_bootstrap()
        assert sandbox_module._bootstrap is not original
        assert sandbox_module._bootstrap._broker is None
    finally:
        sandbox_module._bootstrap = original


def test_install_sandbox_bootstrap_replaces_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install_sandbox_bootstrap() installs the given instance as _bootstrap."""
    original = sandbox_module._bootstrap
    try:
        new_bootstrap = _SandboxBootstrap()
        install_sandbox_bootstrap(new_bootstrap)
        assert sandbox_module._bootstrap is new_bootstrap
    finally:
        sandbox_module._bootstrap = original


def test_fast_path_enabled_with_various_env_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fast_path_enabled returns True for truthy env values only."""
    for truthy in ("1", "true", "True", "TRUE", "yes", "YES"):
        monkeypatch.setenv("ORCHEO_SANDBOX_FAST_PATH_TRUSTED", truthy)
        assert _fast_path_enabled() is True, f"expected True for {truthy!r}"

    for falsy in ("0", "false", "no", "", "maybe"):
        monkeypatch.setenv("ORCHEO_SANDBOX_FAST_PATH_TRUSTED", falsy)
        assert _fast_path_enabled() is False, f"expected False for {falsy!r}"

    monkeypatch.delenv("ORCHEO_SANDBOX_FAST_PATH_TRUSTED", raising=False)
    assert _fast_path_enabled() is False
