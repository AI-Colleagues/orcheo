"""Tests for the backend-shared sandbox bootstrap helpers."""

from __future__ import annotations
import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4
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
    ingest_sandboxed_script,
    is_sandbox_disabled,
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


def test_is_sandbox_disabled_requires_explicit_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runc runtime must not implicitly disable sandboxing."""
    monkeypatch.delenv("ORCHEO_SANDBOX_DISABLED", raising=False)
    monkeypatch.setenv("ORCHEO_CONTAINER_RUNTIME", "runc")
    monkeypatch.setenv("ORCHEO_ENV", "development")
    monkeypatch.setenv("NODE_ENV", "development")

    assert is_sandbox_disabled() is False

    monkeypatch.setenv("ORCHEO_SANDBOX_DISABLED", "true")
    assert is_sandbox_disabled() is True


def test_is_sandbox_disabled_explicit_flag_wins_over_dev_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit false flag should keep sandboxing enabled."""
    monkeypatch.setenv("ORCHEO_SANDBOX_DISABLED", "false")
    monkeypatch.setenv("ORCHEO_CONTAINER_RUNTIME", "runc")

    assert is_sandbox_disabled() is False


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


def test_ensure_sandbox_configured_is_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sandbox bootstrap should not require runtime secrets when disabled."""
    monkeypatch.setenv("ORCHEO_SANDBOX_DISABLED", "true")
    monkeypatch.delenv("ORCHEO_CREDENTIAL_BROKER_SECRET", raising=False)
    monkeypatch.setattr(sandbox_module, "_bootstrap", _SandboxBootstrap())

    ensure_sandbox_configured()


def test_module_level_sandbox_getters_delegate_to_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The module-level helpers should forward directly to the cached bootstrap."""

    class _StubBootstrap:
        def launcher(self) -> str:
            return "launcher"

        def dispatcher(self) -> str:
            return "dispatcher"

    original = sandbox_module._bootstrap
    try:
        sandbox_module._bootstrap = _StubBootstrap()  # type: ignore[assignment]
        assert sandbox_module.get_sandbox_launcher() == "launcher"
        assert sandbox_module.get_sandbox_dispatcher() == "dispatcher"
    finally:
        sandbox_module._bootstrap = original


@pytest.mark.asyncio
async def test_disabled_dispatcher_dispatch_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = sandbox_module._DisabledWorkflowDispatcher()
    spec = build_workflow_run_spec(
        execution_id="exec-1",
        workspace_id="ws-1",
        graph_config={"nodes": []},
        inputs={"input": "value"},
    )

    monkeypatch.setattr(
        sandbox_module,
        "run_in_subprocess",
        lambda *args, **kwargs: {
            "run_id": "exec-1",
            "status": "succeeded",
            "outputs": {"result": "ok"},
            "error": None,
        },
    )

    result = await dispatcher.dispatch(spec)

    assert result.run_id == "exec-1"
    assert result.status == "succeeded"
    assert result.outputs == {"result": "ok"}
    assert result.error is None


@pytest.mark.asyncio
async def test_disabled_dispatcher_dispatch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = sandbox_module._DisabledWorkflowDispatcher()
    spec = build_workflow_run_spec(
        execution_id="exec-2",
        workspace_id="ws-1",
        graph_config={"nodes": []},
        inputs={"input": "value"},
    )

    def _raise(*args: object, **kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(sandbox_module, "run_in_subprocess", _raise)

    result = await dispatcher.dispatch(spec)

    assert result.run_id == "exec-2"
    assert result.status == "failed"
    assert result.outputs == {}
    assert result.error == "RuntimeError: boom"


def test_bootstrap_sync_mode_resets_cached_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _SandboxBootstrap()
    bootstrap._sandbox_disabled = False
    bootstrap._manager = SimpleNamespace()
    bootstrap._launcher = SimpleNamespace()
    bootstrap._dispatcher = SimpleNamespace()
    bootstrap._exec_backend = SimpleNamespace()
    bootstrap._runner = SimpleNamespace()
    bootstrap._ingestor = SimpleNamespace()

    monkeypatch.setattr(sandbox_module, "is_sandbox_disabled", lambda: True)

    assert bootstrap._sync_mode() is True
    assert bootstrap._manager is None
    assert bootstrap._launcher is None
    assert bootstrap._dispatcher is None
    assert bootstrap._exec_backend is None
    assert bootstrap._runner is None
    assert bootstrap._ingestor is None
    assert bootstrap._sandbox_disabled is True


def test_bootstrap_configure_is_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _SandboxBootstrap()
    monkeypatch.setattr(sandbox_module, "is_sandbox_disabled", lambda: True)
    bootstrap.configure(SimpleNamespace())
    assert bootstrap._broker is None


def test_parse_bool_env_handles_blank_and_invalid() -> None:
    assert sandbox_module._parse_bool_env("   ") is None
    assert sandbox_module._parse_bool_env("maybe") is None


def test_sandbox_disabled_bootstrap_uses_local_launcher_and_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled mode should stay fully local and avoid runtime bootstrap."""
    monkeypatch.setenv("ORCHEO_SANDBOX_DISABLED", "true")
    monkeypatch.delenv("ORCHEO_SANDBOX_RUNTIME_URL", raising=False)
    monkeypatch.delenv("ORCHEO_SANDBOX_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("ORCHEO_CREDENTIAL_BROKER_SECRET", raising=False)

    ingested: dict[str, Any] = {}

    def _fake_ingest(
        source: str,
        *,
        entrypoint: str | None,
        max_script_bytes: int | None,
        execution_timeout_seconds: float | None,
    ) -> dict[str, Any]:
        ingested.update(
            {
                "source": source,
                "entrypoint": entrypoint,
                "max_script_bytes": max_script_bytes,
                "execution_timeout_seconds": execution_timeout_seconds,
            }
        )
        return {"format": "langgraph-script", "source": source, "index": {}}

    monkeypatch.setattr(sandbox_module, "ingest_langgraph_script", _fake_ingest)
    monkeypatch.setattr(sandbox_module, "_bootstrap", _SandboxBootstrap())

    launcher = sandbox_module.get_sandbox_launcher()
    launcher_again = sandbox_module.get_sandbox_launcher()
    dispatcher = sandbox_module.get_sandbox_dispatcher()
    dispatcher_again = sandbox_module.get_sandbox_dispatcher()

    from orcheo.sandbox.launcher import LocalProcessLauncher

    assert isinstance(launcher, LocalProcessLauncher)
    assert launcher_again is launcher
    # The disabled dispatcher object still answers "False" for sandbox routing.
    assert dispatcher_again is dispatcher
    assert (
        dispatcher.should_sandbox(
            build_workflow_run_spec(
                execution_id="exec-2",
                workspace_id="ws-1",
                graph_config={"nodes": [{"type": "TenantPythonNode"}]},
                inputs={},
            )
        )
        is False
    )

    result = asyncio.run(
        ingest_sandboxed_script(
            workspace_id="ws-1",
            source="print('hi')",
            entrypoint="build_graph",
        )
    )

    assert result["source"] == "print('hi')"
    assert ingested["entrypoint"] == "build_graph"


def test_build_credential_broker_requires_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The broker refuses to build without ORCHEO_CREDENTIAL_BROKER_SECRET."""
    monkeypatch.delenv("ORCHEO_CREDENTIAL_BROKER_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="ORCHEO_CREDENTIAL_BROKER_SECRET"):
        build_credential_broker()


def test_build_credential_broker_uses_redis_store_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default revocation store is Redis-backed, not process-local."""

    class _FakeRedisStore:
        def __init__(self, redis_url: str) -> None:
            self.redis_url = redis_url

    monkeypatch.setenv("ORCHEO_CREDENTIAL_BROKER_SECRET", "abc")
    monkeypatch.delenv("ORCHEO_SANDBOX_REVOCATION_STORE", raising=False)
    monkeypatch.setattr(sandbox_module, "RedisRevocationStore", _FakeRedisStore)

    broker = build_credential_broker()

    assert isinstance(broker._revocations, _FakeRedisStore)
    assert broker._revocations.redis_url == "redis://redis:6379/0"


def test_build_credential_broker_resolves_credentials_from_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The broker resolver walks the vault metadata and returns the secret."""

    workspace_id = str(uuid4())
    credential_id = uuid4()

    class _Vault:
        def list_credentials(self, *, context, workspace_id=None):
            del context
            assert workspace_id == workspace_id_str
            return [
                SimpleNamespace(id=credential_id, name="openai_api_key"),
                SimpleNamespace(id=uuid4(), name="other"),
            ]

        def reveal_secret(self, *, credential_id, context):
            del context
            assert credential_id == credential_id_expected
            return "secret-value"

    workspace_id_str = workspace_id
    credential_id_expected = credential_id

    monkeypatch.setenv("ORCHEO_CREDENTIAL_BROKER_SECRET", "abc")
    monkeypatch.setenv("ORCHEO_SANDBOX_REVOCATION_STORE", "memory")
    monkeypatch.setattr(sandbox_module, "get_vault", lambda: _Vault())

    broker = build_credential_broker()

    assert (
        broker._resolver(workspace_id=workspace_id, credential_name="openai_api_key")
        == "secret-value"
    )
    with pytest.raises(KeyError):
        broker._resolver(workspace_id=workspace_id, credential_name="missing")


@pytest.mark.asyncio()
async def test_bootstrap_ingest_script_acquires_real_workspace_and_releases_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful ingestion acquires from the real workspace pool and releases."""

    class _FakeLease:
        def __init__(self, sandbox_id: str) -> None:
            self.sandbox_id = sandbox_id

    class _FakeManager:
        def __init__(self) -> None:
            self.acquired: list[tuple[str, str]] = []
            self.released: list[_FakeLease] = []
            self.destroyed: list[_FakeLease] = []

        def acquire(self, workspace_id: str, *, run_id: str) -> _FakeLease:
            self.acquired.append((workspace_id, run_id))
            return _FakeLease("sandbox-1")

        def release(self, lease: _FakeLease) -> None:
            self.released.append(lease)

        def destroy(self, lease: _FakeLease) -> None:
            self.destroyed.append(lease)

    class _FakeIngestor:
        def __init__(self, url: str, *, control_token: str) -> None:
            self.url = url
            self.control_token = control_token
            self.calls: list[tuple[str, str, str | None, int | None, float | None]] = []

        async def ingest(
            self,
            sandbox_id: str,
            *,
            source: str,
            entrypoint: str | None,
            max_script_bytes: int | None,
            execution_timeout_seconds: float | None,
        ) -> dict[str, Any]:
            self.calls.append(
                (
                    sandbox_id,
                    source,
                    entrypoint,
                    max_script_bytes,
                    execution_timeout_seconds,
                )
            )
            return {"ok": True}

    manager = _FakeManager()
    bootstrap = _SandboxBootstrap()
    monkeypatch.setattr(bootstrap, "_ensure_manager", lambda: manager)
    monkeypatch.setattr(bootstrap, "_control_token", lambda: "control-token")
    monkeypatch.setattr(sandbox_module, "RemoteSandboxIngestor", _FakeIngestor)

    result = await bootstrap.ingest_script(
        workspace_id="ws-1",
        source="print('hello')",
        entrypoint="build_graph",
        max_script_bytes=123,
        execution_timeout_seconds=45.0,
    )

    assert result == {"ok": True}
    # Acquires under the real workspace id (not a synthetic ingest:ws-1:uuid key).
    assert manager.acquired[0] == ("ws-1", "script-ingestion")
    # Releases on success — container goes back to the warm pool.
    assert len(manager.released) == 1
    assert len(manager.destroyed) == 0


@pytest.mark.asyncio()
async def test_bootstrap_ingest_script_destroys_lease_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed ingestion destroys the lease rather than returning it to the pool."""

    class _FakeLease:
        def __init__(self, sandbox_id: str) -> None:
            self.sandbox_id = sandbox_id

    class _FakeManager:
        def __init__(self) -> None:
            self.released: list[_FakeLease] = []
            self.destroyed: list[_FakeLease] = []

        def acquire(self, workspace_id: str, *, run_id: str) -> _FakeLease:
            return _FakeLease("sandbox-err")

        def release(self, lease: _FakeLease) -> None:
            self.released.append(lease)

        def destroy(self, lease: _FakeLease) -> None:
            self.destroyed.append(lease)

    class _FailingIngestor:
        async def ingest(self, sandbox_id: str, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("ingestion failed")

    manager = _FakeManager()
    ingestor = _FailingIngestor()
    bootstrap = _SandboxBootstrap()
    bootstrap._ingestor = ingestor  # type: ignore[assignment]
    monkeypatch.setattr(bootstrap, "_ensure_manager", lambda: manager)

    with pytest.raises(RuntimeError, match="ingestion failed"):
        await bootstrap.ingest_script(
            workspace_id="ws-err",
            source="bad code",
            entrypoint=None,
            max_script_bytes=10,
            execution_timeout_seconds=1.0,
        )

    # Destroyed on failure — keeps the pool clean.
    assert len(manager.destroyed) == 1
    assert len(manager.released) == 0


@pytest.mark.asyncio()
async def test_bootstrap_ingest_script_reuses_cached_ingestor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-built ingestor is reused without constructing another one."""

    class _FakeLease:
        def __init__(self, sandbox_id: str) -> None:
            self.sandbox_id = sandbox_id

    class _FakeManager:
        def __init__(self) -> None:
            self.released: list[_FakeLease] = []

        def acquire(self, workspace_id: str, *, run_id: str) -> _FakeLease:
            return _FakeLease("sandbox-2")

        def release(self, lease: _FakeLease) -> None:
            self.released.append(lease)

        def destroy(self, lease: _FakeLease) -> None:
            pass

    class _FakeIngestor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def ingest(
            self,
            sandbox_id: str,
            *,
            source: str,
            entrypoint: str | None,
            max_script_bytes: int | None,
            execution_timeout_seconds: float | None,
        ) -> dict[str, Any]:
            del source, entrypoint, max_script_bytes, execution_timeout_seconds
            self.calls.append(sandbox_id)
            return {"ok": True}

    manager = _FakeManager()
    ingestor = _FakeIngestor()
    bootstrap = _SandboxBootstrap()
    bootstrap._ingestor = ingestor  # type: ignore[assignment]
    monkeypatch.setattr(bootstrap, "_ensure_manager", lambda: manager)

    result = await bootstrap.ingest_script(
        workspace_id="ws-2",
        source="print('hello')",
        entrypoint=None,
        max_script_bytes=10,
        execution_timeout_seconds=1.0,
    )

    assert result == {"ok": True}
    assert ingestor.calls == ["sandbox-2"]
    assert len(manager.released) == 1


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


def test_bootstrap_ensure_manager_creates_remote_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_manager lazily creates a RemoteSandboxManager."""
    monkeypatch.setenv("ORCHEO_SANDBOX_RUNTIME_URL", "http://sandbox-runtime:9090")

    created_managers: list[Any] = []

    class _FakeRemoteManager:
        def __init__(self, url: str, *, control_token: str) -> None:
            self.url = url
            assert control_token == "test-sandbox-control-token"
            created_managers.append(self)

    monkeypatch.setattr(sandbox_module, "RemoteSandboxManager", _FakeRemoteManager)

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

    class _FakeRemoteManager:
        def __init__(self, url: str, *, control_token: str) -> None:
            pass

    class _FakeExec:
        def __init__(self, url: str, *, control_token: str) -> None:
            self.url = url
            assert control_token == "test-sandbox-control-token"

    class _FakeLauncher:
        def __init__(self, manager: Any, *, exec_backend: Any) -> None:
            self.exec_backend = exec_backend

    monkeypatch.setattr(sandbox_module, "RemoteSandboxManager", _FakeRemoteManager)
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

    class _FakeRemoteManager:
        def __init__(self, url: str, *, control_token: str) -> None:
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

    monkeypatch.setattr(sandbox_module, "RemoteSandboxManager", _FakeRemoteManager)
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
