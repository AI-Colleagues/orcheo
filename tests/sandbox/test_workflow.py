"""Tests for the workflow dispatcher and runner."""

from __future__ import annotations
import asyncio
from collections.abc import Mapping
from typing import Any
import json
import pytest
import httpx
from langchain_core.messages import HumanMessage
from orcheo.runtime.credentials import (
    UnknownCredentialPayloadError,
    credential_ref,
    get_active_credential_resolver,
)
from orcheo.sandbox.broker import CredentialBroker
from orcheo.sandbox.config import SandboxSettings
from orcheo.sandbox.manager import SandboxRuntimeManager
from orcheo.sandbox.models import SandboxLease, SandboxState
from orcheo.sandbox.runtime import InMemoryContainerRuntime
from orcheo.sandbox.workflow import (
    TRUSTED_NODE_TYPES,
    WorkflowRunResult,
    WorkflowRunSpec,
    WorkflowSandboxDispatcher,
    requires_sandbox,
)
from orcheo.sandbox.workflow_runner import run_in_subprocess


def _broker() -> CredentialBroker:
    return CredentialBroker(secret="s", resolver=lambda **_: "v")


def _manager() -> tuple[SandboxRuntimeManager, InMemoryContainerRuntime]:
    runtime = InMemoryContainerRuntime()
    return (
        SandboxRuntimeManager(
            runtime=runtime,
            settings=SandboxSettings(),
        ),
        runtime,
    )


class _RecordingRunner:
    """Runner that records calls and returns a configurable result."""

    def __init__(
        self,
        result: WorkflowRunResult | None = None,
        raise_on_call: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[SandboxLease, WorkflowRunSpec, str]] = []
        self.result = result
        self.raise_on_call = raise_on_call

    async def execute(
        self,
        lease: SandboxLease,
        spec: WorkflowRunSpec,
        broker_token: str,
    ) -> WorkflowRunResult:
        self.calls.append((lease, spec, broker_token))
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return self.result or WorkflowRunResult(
            run_id=spec.run_id,
            status="succeeded",
            outputs={"ok": True},
        )


def test_requires_sandbox_flags_unknown_node_types() -> None:
    """Any node outside the trusted set forces a sandbox route."""
    assert requires_sandbox(("AINode", "TenantPythonNode"))
    assert not requires_sandbox(tuple(TRUSTED_NODE_TYPES))


def test_requires_sandbox_fails_closed_on_empty_node_types() -> None:
    """An unparseable graph (no node types) must route to the sandbox."""
    assert requires_sandbox(())


def test_trusted_set_excludes_code_bearing_base_types() -> None:
    """Base / code-bearing node types must not appear in the trusted set."""
    forbidden = {"TaskNode", "BaseNode", "IntegrationNode", "DataTransformNode"}
    assert forbidden.isdisjoint(TRUSTED_NODE_TYPES)


def test_dispatcher_routes_through_sandbox_by_default() -> None:
    """With fast-path off, every run acquires a workflow sandbox."""
    manager, runtime = _manager()
    runner = _RecordingRunner()
    dispatcher = WorkflowSandboxDispatcher(
        manager=manager,
        runner=runner,
        broker=_broker(),
    )
    spec = WorkflowRunSpec(
        run_id="r1",
        workspace_id="ws",
        workflow_definition={"nodes": []},
        inputs={"x": 1},
        node_types=("AINode",),
    )
    result = asyncio.run(dispatcher.dispatch(spec))
    assert result.status == "succeeded"
    assert len(runtime.started) == 1
    assert runner.calls[0][1].run_id == "r1"
    assert runner.calls[0][2]  # broker token populated


def test_dispatcher_fast_path_skips_sandbox_for_trusted_only() -> None:
    """Trusted-only workflows skip the sandbox when fast-path is enabled."""
    manager, runtime = _manager()
    runner = _RecordingRunner()
    dispatcher = WorkflowSandboxDispatcher(
        manager=manager,
        runner=runner,
        broker=_broker(),
        allow_in_worker_fast_path=True,
    )
    spec = WorkflowRunSpec(
        run_id="r2",
        workspace_id="ws",
        workflow_definition={"nodes": []},
        inputs={},
        node_types=("AINode", "ChatModelNode"),
    )
    asyncio.run(dispatcher.dispatch(spec))
    assert runtime.started == []
    assert runner.calls[0][2] == ""  # no broker token for fast path


def test_dispatcher_releases_sandbox_and_revokes_token_on_runner_error() -> None:
    """A runner exception still releases the sandbox and revokes the token."""
    manager, runtime = _manager()
    broker = _broker()
    runner = _RecordingRunner(raise_on_call=RuntimeError("boom"))
    dispatcher = WorkflowSandboxDispatcher(
        manager=manager, runner=runner, broker=broker
    )
    spec = WorkflowRunSpec(
        run_id="r3",
        workspace_id="ws",
        workflow_definition={},
        inputs={},
        node_types=("UnknownNode",),
    )
    result = asyncio.run(dispatcher.dispatch(spec))
    assert result.status == "failed"
    assert result.error == "RuntimeError: boom"
    assert len(runtime.started) == 1
    # The sandbox should have been released back to the pool, not destroyed.
    assert len(runtime.stopped) == 0
    # The broker should have marked the run as revoked.
    token = broker.issue(workspace_id="ws", run_id="r3")
    # The previously issued token had a different timestamp; verify by issuing
    # then trying to parse — revocation is keyed by run_id, so any future use
    # of this run_id is rejected.
    with pytest.raises(Exception):
        broker.parse(token)


def test_run_in_subprocess_returns_failure_for_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure in the run-graph implementation is surfaced cleanly."""

    def _fail(
        definition: Mapping[str, Any],
        inputs: Mapping[str, Any],
        **_: object,
    ) -> Mapping[str, Any]:
        del definition, inputs
        raise RuntimeError("graph blew up")

    monkeypatch.setattr("orcheo.sandbox.workflow_runner._run_graph", _fail)
    result = run_in_subprocess({}, {}, spawn=False)
    assert result["status"] == "failed"
    assert "graph blew up" in result["error"]


def test_run_in_subprocess_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful run produces a succeeded result with outputs."""

    def _ok(
        definition: Mapping[str, Any],
        inputs: Mapping[str, Any],
        **_: object,
    ) -> Mapping[str, Any]:
        del definition
        return {"sum": inputs["a"] + inputs["b"]}

    monkeypatch.setattr("orcheo.sandbox.workflow_runner._run_graph", _ok)
    result = run_in_subprocess({}, {"a": 2, "b": 3}, spawn=False)
    assert result["status"] == "succeeded"
    assert result["outputs"]["sum"] == 5


def test_workflow_result_json_serializes_langchain_messages() -> None:
    """Sandbox results retain LangChain message fields across JSON transport."""
    from orcheo.sandbox import workflow_runner

    payload = json.loads(
        json.dumps(
            {"messages": [HumanMessage(content="hello")]},
            default=workflow_runner._json_default,
        )
    )

    assert payload["messages"][0]["type"] == "human"
    assert payload["messages"][0]["content"] == "hello"


def test_should_sandbox_obeys_fast_path_flag() -> None:
    """should_sandbox returns True when fast path is disabled."""
    manager, _ = _manager()
    dispatcher = WorkflowSandboxDispatcher(
        manager=manager,
        runner=_RecordingRunner(),
        broker=_broker(),
        allow_in_worker_fast_path=False,
    )
    spec = WorkflowRunSpec(
        run_id="r4",
        workspace_id="ws",
        workflow_definition={},
        inputs={},
        node_types=("AINode",),
    )
    assert dispatcher.should_sandbox(spec)


def test_synthetic_lease_used_when_fast_path_engages() -> None:
    """Fast path uses an in-worker synthetic lease so the runner contract holds."""
    manager, _ = _manager()
    runner = _RecordingRunner()
    dispatcher = WorkflowSandboxDispatcher(
        manager=manager,
        runner=runner,
        broker=_broker(),
        allow_in_worker_fast_path=True,
    )
    spec = WorkflowRunSpec(
        run_id="r5",
        workspace_id="ws",
        workflow_definition={},
        inputs={},
        node_types=("AINode",),
    )
    asyncio.run(dispatcher.dispatch(spec))
    lease = runner.calls[0][0]
    assert lease.sandbox_id == "in-worker"
    assert lease.state is SandboxState.IN_USE


def test_run_graph_delegates_to_build_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_graph imports build_graph lazily and async-invokes the graph."""
    from orcheo.sandbox import workflow_runner

    fake_outputs: Mapping[str, Any] = {"answer": 42}
    captured: dict[str, object] = {}

    class _FakeCompiled:
        async def ainvoke(
            self,
            inputs: object,
            *,
            config: object,
        ) -> Mapping[str, Any]:
            captured["inputs"] = inputs
            captured["config"] = config
            return fake_outputs

    class _FakeGraph:
        def compile(self, **kwargs: object) -> _FakeCompiled:
            captured["compile_kwargs"] = kwargs
            return _FakeCompiled()

    import orcheo.graph.builder as _builder_module

    monkeypatch.setattr(_builder_module, "build_graph", lambda _d: _FakeGraph())

    result = workflow_runner._run_graph(
        {"format": "langgraph-script"},
        {"x": 1},
        runnable_config={"configurable": {"thread_id": "run-1"}},
        state_config={"configurable": {"ai_model": "openai:test"}},
        workspace_id="ws-1",
    )
    assert result == fake_outputs
    assert captured == {
        "compile_kwargs": {
            "store": captured["compile_kwargs"]["store"],
        },
        "inputs": {
            "x": 1,
            "inputs": {"x": 1},
            "results": {},
            "messages": [],
            "workspace_id": "ws-1",
            "config": {"configurable": {"ai_model": "openai:test"}},
        },
        "config": {"configurable": {"thread_id": "run-1"}},
    }


def test_broker_credential_context_resolves_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sandbox credential placeholders resolve through the broker HTTP API."""
    from orcheo.sandbox import workflow_runner

    calls: list[tuple[str, dict[str, object], dict[str, str]]] = []

    def _post(
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        assert timeout == 30.0
        calls.append((url, json, headers))
        return httpx.Response(200, json={"value": "vault-secret"})

    monkeypatch.setenv("ORCHEO_BROKER_TOKEN", "broker-token")
    monkeypatch.setenv("ORCHEO_CREDENTIAL_BROKER_URL", "http://runtime/broker")
    monkeypatch.setattr(workflow_runner.httpx, "post", _post)

    with workflow_runner._credential_context(run_id="run-1", workspace_id="ws-1"):
        resolver = get_active_credential_resolver()
        assert resolver is not None
        assert resolver.resolve(credential_ref("openai_api_key")) == "vault-secret"

    assert calls == [
        (
            "http://runtime/broker",
            {"run_id": "run-1", "credential_name": "openai_api_key"},
            {
                "Authorization": "Bearer broker-token",
                "X-Orcheo-Workspace": "ws-1",
            },
        )
    ]


def test_broker_credential_resolver_rejects_non_secret_payload() -> None:
    """The sandbox broker channel rejects payload shapes it cannot return."""
    from orcheo.sandbox import workflow_runner

    resolver = workflow_runner._BrokerCredentialResolver(
        broker_url="http://runtime/broker",
        broker_token="token",
        run_id="run-1",
        workspace_id="ws-1",
    )

    with pytest.raises(UnknownCredentialPayloadError, match="only supports secret"):
        resolver.resolve(credential_ref("oauth-ref", payload="oauth.access_token"))


def test_run_in_subprocess_spawn_mode_creates_child_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_in_subprocess(spawn=True) delegates execution to a child process."""
    import multiprocessing as _mp
    from orcheo.sandbox import workflow_runner

    fake_result: Mapping[str, Any] = {
        "status": "succeeded",
        "outputs": {},
        "error": None,
    }
    calls: list[str] = []

    class _FakeQueue:
        def get_nowait(self) -> Mapping[str, Any]:
            return fake_result

    class _FakeProcess:
        def start(self) -> None:
            calls.append("start")

        def join(self) -> None:
            calls.append("join")

    class _FakeContext:
        def Queue(self) -> _FakeQueue:
            return _FakeQueue()

        def Process(self, **kwargs: object) -> _FakeProcess:
            calls.append("Process")
            return _FakeProcess()

    class _FakeMp:
        @staticmethod
        def get_context(ctx: str) -> _FakeContext:
            calls.append(f"get_context:{ctx}")
            return _FakeContext()

    monkeypatch.setattr(workflow_runner, "mp", _FakeMp)

    result = workflow_runner.run_in_subprocess({}, {}, spawn=True)
    assert result == fake_result
    assert "get_context:spawn" in calls
    assert "start" in calls
    assert "join" in calls
