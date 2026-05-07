import asyncio
import os
from collections.abc import Mapping
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import UUID
import pytest
from chatkit.errors import CustomStreamError
from langchain_core.messages import AIMessage, HumanMessage
from orcheo_backend.app.chatkit import workflow_executor as workflow_executor_module
from orcheo_backend.app.chatkit.workflow_executor import (
    WorkflowExecutor,
    _append_chatkit_history_step,
    _build_reply_state,
    _external_agent_provider_environment,
    _mark_chatkit_history_completed,
    _mark_chatkit_history_failed,
    _patched_environment,
    _resolve_runtime_thread_id,
    _start_chatkit_history,
    _with_chatkit_model,
    _with_thread_id,
)
from orcheo_backend.app.history.models import RunHistoryError
from orcheo_backend.app.schemas.system import ExternalAgentProviderName


class DummyHistoryStore:
    def __init__(
        self, *, raise_on_start=False, raise_on_append=False, raise_on_mark=False
    ):
        self.raise_on_start = raise_on_start
        self.raise_on_append = raise_on_append
        self.raise_on_mark = raise_on_mark
        self.started = False
        self.appended = []
        self.completed = False
        self.failed = False

    async def start_run(self, **kwargs) -> None:
        if self.raise_on_start:
            raise RunHistoryError("boom")
        self.started = True

    async def append_step(
        self, execution_id: str, payload: Mapping[str, object]
    ) -> None:
        if self.raise_on_append:
            raise RunHistoryError("boom")
        self.appended.append((execution_id, dict(payload)))

    async def mark_completed(self, execution_id: str) -> None:
        if self.raise_on_mark:
            raise RunHistoryError("boom")
        self.completed = True

    async def mark_failed(self, execution_id: str, error: str) -> None:
        if self.raise_on_mark:
            raise RunHistoryError("boom")
        self.failed = True


data_config = type("DataConfig", (), {})


class DummyRunnableConfig:
    tags = []
    callbacks = []
    metadata = {}
    run_name = "run"

    def to_json_config(self, execution_id: str) -> Mapping[str, str]:
        return {"execution_id": execution_id}


@pytest.mark.asyncio
async def test_start_chatkit_history_records_run():
    history = DummyHistoryStore()
    config = DummyRunnableConfig()
    await _start_chatkit_history(
        history_store=history,
        workflow_id=UUID(int=0),
        execution_id="exec",
        runtime_thread_id="thread",
        inputs={"foo": "bar"},
        merged_config=config,
    )
    assert history.started


@pytest.mark.asyncio
async def test_start_chatkit_history_handles_errors(caplog):
    history = DummyHistoryStore(raise_on_start=True)
    config = DummyRunnableConfig()
    await _start_chatkit_history(
        history_store=history,
        workflow_id=UUID(int=0),
        execution_id="exec",
        runtime_thread_id="thread",
        inputs={"foo": "bar"},
        merged_config=config,
    )
    assert "Failed to start" in caplog.text


@pytest.mark.asyncio
async def test_append_chatkit_history_step():
    history = DummyHistoryStore()
    await _append_chatkit_history_step(history, "exec", {"foo": "bar"})
    assert history.appended


@pytest.mark.asyncio
async def test_append_chatkit_history_step_handles_error(caplog):
    history = DummyHistoryStore(raise_on_append=True)
    await _append_chatkit_history_step(history, "exec", {"foo": "bar"})
    assert "Failed to append" in caplog.text


@pytest.mark.asyncio
async def test_mark_chatkit_history_completed():
    history = DummyHistoryStore()
    await _mark_chatkit_history_completed(history, "exec")
    assert history.completed


@pytest.mark.asyncio
async def test_mark_chatkit_history_completed_handles_error(caplog):
    history = DummyHistoryStore(raise_on_mark=True)
    await _mark_chatkit_history_completed(history, "exec")
    assert "Failed to mark chatkit history completed" in caplog.text


@pytest.mark.asyncio
async def test_mark_chatkit_history_failed():
    history = DummyHistoryStore()
    await _mark_chatkit_history_failed(history, "exec", "boom")
    assert history.failed


@pytest.mark.asyncio
async def test_mark_chatkit_history_failed_handles_error(caplog):
    history = DummyHistoryStore(raise_on_mark=True)
    await _mark_chatkit_history_failed(history, "exec", "boom")
    assert "Failed to mark chatkit history failed" in caplog.text


def test_patched_environment_restores_value(tmp_path):
    os_env_key = "TEST_CHATKIT"
    original = os.environ.get(os_env_key)
    with _patched_environment({os_env_key: "value"}):
        assert os.environ[os_env_key] == "value"
    assert os.environ.get(os_env_key) == original


def test_patched_environment_restores_existing_value(monkeypatch):
    os_env_key = "TEST_CHATKIT_EXISTING"
    monkeypatch.setenv(os_env_key, "original")
    with _patched_environment({os_env_key: "new"}):
        assert os.environ[os_env_key] == "new"
    assert os.environ[os_env_key] == "original"


def test_with_thread_id_injects():
    config = {"configurable": {"foo": "bar"}}
    result = _with_thread_id(config, "abc")
    assert result["configurable"]["thread_id"] == "abc"


def test_with_chatkit_model_inserts_and_removes():
    config = {"configurable": {}}
    with_model = _with_chatkit_model(config, "gpt-4")
    assert with_model["configurable"]["chatkit_model"] == "gpt-4"
    without = _with_chatkit_model(with_model, None)
    assert "chatkit_model" not in without["configurable"]


def test_resolve_runtime_thread_id_prefers_inputs():
    assert _resolve_runtime_thread_id({"thread_id": " id "}, "exec") == "id"


def test_resolve_runtime_thread_id_falls_back():
    assert _resolve_runtime_thread_id({}, "exec") == "exec"


def test_resolve_runtime_thread_id_uses_session_id_when_thread_id_is_blank():
    assert (
        _resolve_runtime_thread_id({"thread_id": "   ", "session_id": "sess"}, "exec")
        == "sess"
    )


def test_external_agent_provider_environment(monkeypatch):
    class DummyStore:
        def __init__(self):
            self.calls = []

        def get_provider_environment(self, provider, workspace_id=None):
            self.calls.append((provider, workspace_id))
            return {provider.name: "ok"}

    store = DummyStore()
    workspace_id = "workspace-1"

    monkeypatch.setattr(
        workflow_executor_module,
        "get_external_agent_runtime_store",
        lambda: store,
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "list_external_agent_providers",
        lambda: [ExternalAgentProviderName.CLAUDE_CODE],
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "load_external_agent_vault_environment",
        lambda vault, workspace_id=None: {},
    )
    env = _external_agent_provider_environment(workspace_id)
    assert env == {"CLAUDE_CODE": "ok"}
    assert store.calls == [(ExternalAgentProviderName.CLAUDE_CODE, workspace_id)]


def test_build_reply_state_and_extract_messages():
    final_state = {"reply": "hi", "messages": [HumanMessage(content="hello")]}
    reply, state = _build_reply_state(final_state)
    assert reply == "hi"
    assert state.get("_messages")


def test_build_reply_state_missing_reply():
    with pytest.raises(CustomStreamError):
        _build_reply_state({})


def test_extract_messages_reads_object_attribute_messages() -> None:
    final_state = SimpleNamespace(messages=[AIMessage(content="hello"), object()])
    assert WorkflowExecutor._extract_messages(final_state) == [
        AIMessage(content="hello")
    ]


def test_build_step_callback_invokes(monkeypatch):
    history = DummyHistoryStore()
    called = []

    async def progress(step):
        called.append(step)

    executor = WorkflowExecutor(repository=object(), vault_provider=lambda: object())
    callback = executor._build_step_callback(
        history_store=history,
        execution_id="exec",
        progress_callback=progress,
    )

    asyncio.run(callback({"node": "test"}))
    assert called


def test_with_chatkit_model_replaces_non_mapping_configurable() -> None:
    result = _with_chatkit_model({"configurable": "invalid"}, "gpt-5")
    assert result["configurable"]["chatkit_model"] == "gpt-5"


def test_with_chatkit_model_without_selection_replaces_non_mapping_configurable() -> (
    None
):
    result = _with_chatkit_model({"configurable": "invalid"}, None)
    assert result["configurable"] == {}


@pytest.mark.asyncio
async def test_build_step_callback_skips_none_progress_callback() -> None:
    history = DummyHistoryStore()
    executor = WorkflowExecutor(repository=object(), vault_provider=lambda: object())
    callback = executor._build_step_callback(
        history_store=history,
        execution_id="exec",
        progress_callback=None,
    )

    await callback({"node": "test"})

    assert history.appended == [("exec", {"node": "test"})]


@pytest.mark.asyncio
async def test_execute_graph_streams_updates_with_step_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_steps: list[Mapping[str, object]] = []
    progress_context_events: list[str] = []

    class DummyCompiled:
        async def astream(self, payload, *, config, stream_mode):
            assert payload == {
                "inputs": {"message": "hello"},
                "workspace_id": None,
            }
            assert config == {"configurable": {"thread_id": "thread"}}
            assert stream_mode == "updates"
            yield {"node": {"status": "running"}}

        async def aget_state(self, config):
            assert config == {"configurable": {"thread_id": "thread"}}
            return SimpleNamespace(values={"reply": "done"})

    class DummyGraph:
        def compile(self, *, checkpointer, store):
            assert checkpointer == "checkpointer"
            assert store == "graph-store"
            return DummyCompiled()

    class DummyAsyncContext:
        def __init__(self, value: object) -> None:
            self._value = value

        async def __aenter__(self) -> object:
            return self._value

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class ProgressContext:
        def __enter__(self) -> None:
            progress_context_events.append("enter")

        def __exit__(self, exc_type, exc, tb) -> None:
            progress_context_events.append("exit")

    monkeypatch.setattr(workflow_executor_module, "get_settings", lambda: {})
    monkeypatch.setattr(
        workflow_executor_module,
        "create_checkpointer",
        lambda settings: DummyAsyncContext("checkpointer"),
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "create_graph_store",
        lambda settings: DummyAsyncContext("graph-store"),
    )
    monkeypatch.setattr(
        workflow_executor_module, "build_graph", lambda graph: DummyGraph()
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "build_initial_state",
        lambda graph_config, inputs, runtime_config=None, workspace_id=None: {
            "inputs": dict(inputs),
            "workspace_id": workspace_id,
        },
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "CredentialResolver",
        lambda vault, context=None: object(),
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "credential_resolution",
        lambda resolver: nullcontext(),
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "_external_agent_provider_environment",
        lambda workspace_id=None: {
            "EXTERNAL_AGENT_TOKEN": "secret",
            "WORKSPACE_ID": workspace_id or "",
        },
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "tool_progress_context",
        lambda callback: ProgressContext(),
    )

    executor = WorkflowExecutor(repository=object(), vault_provider=lambda: object())
    result = await executor._execute_graph(
        workflow_id=UUID(int=0),
        graph_config={"nodes": []},
        inputs={"message": "hello"},
        config={"configurable": {"thread_id": "thread"}},
        state_config={"configurable": {"thread_id": "thread"}},
        step_callback=lambda step: captured_steps.append(step) or asyncio.sleep(0),
    )

    assert result == {"reply": "done"}
    assert captured_steps == [{"node": {"status": "running"}}]
    assert progress_context_events == ["enter", "exit"]


@pytest.mark.asyncio
async def test_record_run_failure_skips_repository_update_without_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = DummyHistoryStore()
    recorded: list[tuple[str, str]] = []

    async def fake_mark_failed(store, execution_id: str, error_message: str) -> None:
        recorded.append((execution_id, error_message))

    monkeypatch.setattr(
        workflow_executor_module,
        "_mark_chatkit_history_failed",
        fake_mark_failed,
    )

    executor = WorkflowExecutor(repository=object(), vault_provider=lambda: object())
    await executor._record_run_failure(
        run=None,
        actor="chatkit",
        history_store=history,
        execution_id="exec-none",
        error_message="boom",
    )

    assert recorded == [("exec-none", "boom")]


@pytest.mark.asyncio
async def test_run_builds_step_callback_when_progress_callback_is_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = SimpleNamespace(chatkit=None)
    version = SimpleNamespace(
        id=UUID(int=2),
        graph={"nodes": []},
        runnable_config={},
    )

    class Repository:
        async def get_workflow(self, workflow_id):
            return workflow

        async def get_latest_version(self, workflow_id):
            return version

    class DummyMergedConfig:
        tags: list[object] = []
        callbacks: list[object] = []
        metadata: dict[str, object] = {}
        run_name = None

        def to_runnable_config(self, execution_id: str) -> dict[str, object]:
            return {"configurable": {"thread_id": execution_id}}

        def to_state_config(self, execution_id: str) -> dict[str, object]:
            return {"configurable": {"thread_id": execution_id}}

        def to_json_config(self, execution_id: str) -> dict[str, object]:
            return {"configurable": {"thread_id": execution_id}}

    build_step_callback_calls: list[tuple[object, str, object]] = []
    execution_args: dict[str, object] = {}
    progress_callback = object()
    step_callback = object()
    history_store = object()

    monkeypatch.setattr(
        workflow_executor_module,
        "get_history_store",
        lambda: history_store,
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "apply_chatkit_selected_model",
        lambda inputs, workflow: None,
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "merge_runnable_configs",
        lambda stored, override: DummyMergedConfig(),
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "_start_chatkit_history",
        lambda **kwargs: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        WorkflowExecutor,
        "_create_run_record",
        lambda self, workflow_id, workflow_version_id, actor, inputs, **kwargs: (
            execution_args.update(kwargs) or asyncio.sleep(0, result=None)
        ),
    )
    monkeypatch.setattr(
        WorkflowExecutor, "_resolve_execution_id", staticmethod(lambda run: "exec-1")
    )
    monkeypatch.setattr(
        WorkflowExecutor,
        "_build_step_callback",
        lambda self, *, history_store, execution_id, progress_callback: (
            build_step_callback_calls.append(
                (history_store, execution_id, progress_callback)
            )
            or step_callback
        ),
    )

    async def fake_execute_graph(self, **kwargs):
        execution_args.update(kwargs)
        return {"reply": "ok"}

    monkeypatch.setattr(WorkflowExecutor, "_execute_graph", fake_execute_graph)
    monkeypatch.setattr(
        workflow_executor_module,
        "_build_reply_state",
        lambda final_state: ("ok", final_state),
    )
    monkeypatch.setattr(
        workflow_executor_module,
        "_mark_chatkit_history_completed",
        lambda history_store, execution_id: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        WorkflowExecutor,
        "_mark_run_succeeded",
        lambda self, run, actor, reply: asyncio.sleep(0),
    )

    executor = WorkflowExecutor(
        repository=Repository(), vault_provider=lambda: object()
    )
    reply, state_view, run = await executor.run(
        UUID(int=1),
        {"message": "hello"},
        progress_callback=progress_callback,  # type: ignore[arg-type]
        workspace_id="workspace-1",
    )

    assert reply == "ok"
    assert state_view == {"reply": "ok"}
    assert run is None
    assert build_step_callback_calls == [(history_store, "exec-1", progress_callback)]
    assert execution_args["step_callback"] is step_callback
    assert execution_args["workspace_id"] == "workspace-1"
