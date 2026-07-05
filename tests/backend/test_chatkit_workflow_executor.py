import asyncio
from collections.abc import Mapping
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import UUID
import pytest
from chatkit.errors import CustomStreamError
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from unittest.mock import AsyncMock
from orcheo_backend.app.chatkit import workflow_executor as workflow_executor_module
from orcheo_backend.app.chatkit.workflow_executor import (
    _NEW_MESSAGES_KEY,
    WorkflowExecutor,
    _annotate_new_messages,
    _append_chatkit_history_step,
    _build_reply_state,
    _mark_chatkit_history_completed,
    _mark_chatkit_history_failed,
    _resolve_runtime_thread_id,
    _start_chatkit_history,
    _with_chatkit_model,
    _with_thread_id,
)
from orcheo_backend.app.history.models import RunHistoryError
from orcheo_backend.app.repository import WorkflowNotFoundError


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


def test_build_attachment_config_returns_empty_without_service() -> None:
    executor = WorkflowExecutor(
        SimpleNamespace(), lambda: None, attachment_service=None
    )

    assert (
        executor._build_attachment_config(
            workspace_id=None,
            workflow_id="wf",
            thread_id="thread",
            upload_session_id=None,
        )
        == {}
    )
    assert (
        executor._build_attachment_config(
            workspace_id="ws",
            workflow_id="wf",
            thread_id="thread",
            upload_session_id=None,
        )
        == {}
    )


def test_build_attachment_config_includes_helpers() -> None:
    attachment_service = SimpleNamespace(
        blob_backend="postgres",
        load_attachment_bytes=AsyncMock(),
        save_attachment=AsyncMock(),
    )
    executor = WorkflowExecutor(
        SimpleNamespace(),
        lambda: None,
        attachment_service=attachment_service,
    )

    result = executor._build_attachment_config(
        workspace_id="ws",
        workflow_id="wf",
        thread_id="thread",
        upload_session_id="ups",
    )

    assert set(result) == {
        "attachment_resolver",
        "attachment_scope",
        "attachment_uploader",
    }
    assert result["attachment_scope"].workspace_id == "ws"


def test_with_attachment_scope_merges_extras() -> None:
    config = {"configurable": {"existing": "value"}}
    extras = {"attachment_resolver": object(), "attachment_scope": object()}

    result = workflow_executor_module._with_attachment_scope(config, extras)

    assert result["configurable"]["existing"] == "value"
    assert (
        result["configurable"]["attachment_resolver"] is extras["attachment_resolver"]
    )
    assert result["configurable"]["attachment_scope"] is extras["attachment_scope"]


def test_with_attachment_scope_replaces_non_mapping_configurable() -> None:
    config = {"configurable": "not-a-mapping"}
    extras = {"attachment_resolver": object()}

    result = workflow_executor_module._with_attachment_scope(config, extras)

    assert result["configurable"] == extras


def test_with_attachment_scope_returns_copy_without_extras() -> None:
    config = {"configurable": {"existing": "value"}}
    result = workflow_executor_module._with_attachment_scope(config, {})

    assert result == config
    assert result is not config


def test_resolve_runtime_thread_id_prefers_inputs():
    assert _resolve_runtime_thread_id({"thread_id": " id "}, "exec") == "id"


def test_resolve_runtime_thread_id_falls_back():
    assert _resolve_runtime_thread_id({}, "exec") == "exec"


def test_resolve_runtime_thread_id_uses_session_id_when_thread_id_is_blank():
    assert (
        _resolve_runtime_thread_id({"thread_id": "   ", "session_id": "sess"}, "exec")
        == "sess"
    )


def test_build_reply_state_and_extract_messages():
    final_state = {"reply": "hi", "messages": [HumanMessage(content="hello")]}
    reply, state = _build_reply_state(final_state)
    assert reply == "hi"
    assert state.get("_messages")


def test_annotate_new_messages_slices_off_prior_turns():
    prior = [HumanMessage(content="old"), AIMessage(content="old reply")]
    new = [HumanMessage(content="new"), AIMessage(content="new reply")]
    state = {"messages": prior + new}

    annotated = _annotate_new_messages(state, prior_count=len(prior))

    assert annotated[_NEW_MESSAGES_KEY] == new
    # The full history is preserved under the original key.
    assert annotated["messages"] == prior + new


def test_annotate_new_messages_falls_back_when_history_shrank():
    messages = [HumanMessage(content="only")]
    state = {"messages": messages}

    annotated = _annotate_new_messages(state, prior_count=5)

    assert annotated[_NEW_MESSAGES_KEY] == messages


def test_annotate_new_messages_ignores_states_without_messages():
    state = {"reply": "done"}

    assert _annotate_new_messages(state, prior_count=0) == {"reply": "done"}


def test_build_reply_state_scopes_messages_to_current_turn():
    """Widget hydration must only see the current turn's messages."""
    prior = [HumanMessage(content="old"), AIMessage(content="old reply")]
    new = [HumanMessage(content="new"), AIMessage(content="new reply")]
    final_state = {
        "reply": "new reply",
        "messages": prior + new,
        _NEW_MESSAGES_KEY: new,
    }

    reply, state = _build_reply_state(final_state)

    assert reply == "new reply"
    assert state["_messages"] == new
    # The internal annotation key is not leaked downstream.
    assert _NEW_MESSAGES_KEY not in state


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
        async def astream(self, payload, *, config, stream_mode, subgraphs=False):
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
async def test_execute_graph_annotates_current_turn_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The streaming path slices accumulated history down to this turn."""
    prior = [HumanMessage(content="old"), AIMessage(content="old reply")]
    new = [HumanMessage(content="new"), AIMessage(content="new reply")]

    class DummyCompiled:
        def __init__(self) -> None:
            self._streamed = False

        async def astream(self, payload, *, config, stream_mode, subgraphs=False):
            self._streamed = True
            yield {"node": {"status": "running"}}

        async def aget_state(self, config):
            # Before streaming: only the prior turns are persisted. After
            # streaming: the full accumulated history.
            values = (
                {"messages": prior + new}
                if self._streamed
                else {"messages": list(prior)}
            )
            return SimpleNamespace(values=values)

    class DummyGraph:
        def compile(self, *, checkpointer, store):
            return DummyCompiled()

    class DummyAsyncContext:
        def __init__(self, value: object) -> None:
            self._value = value

        async def __aenter__(self) -> object:
            return self._value

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

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
        lambda graph_config, inputs, runtime_config=None, workspace_id=None: {},
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
        "tool_progress_context",
        lambda callback: nullcontext(),
    )

    executor = WorkflowExecutor(repository=object(), vault_provider=lambda: object())
    result = await executor._execute_graph(
        workflow_id=UUID(int=0),
        graph_config={"nodes": []},
        inputs={"message": "new"},
        config={"configurable": {"thread_id": "thread"}},
        state_config={"configurable": {"thread_id": "thread"}},
        step_callback=lambda step: asyncio.sleep(0),
    )

    assert result["messages"] == prior + new
    assert result[_NEW_MESSAGES_KEY] == new


@pytest.mark.asyncio
async def test_execute_graph_surfaces_new_interrupt_as_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LangGraph interrupt should become a ChatKit assistant prompt."""

    class DummyInterrupt:
        value = {"message": "What is your guess?"}

    class DummyCompiled:
        async def astream(self, payload, *, config, stream_mode, subgraphs=False):
            yield {"__interrupt__": (DummyInterrupt(),)}

        async def aget_state(self, config):
            return SimpleNamespace(
                values={"node_results": {"agent": {"branch": "human"}}},
                interrupts=(DummyInterrupt(),),
            )

    class DummyGraph:
        def compile(self, *, checkpointer, store):
            return DummyCompiled()

    class DummyAsyncContext:
        def __init__(self, value: object) -> None:
            self._value = value

        async def __aenter__(self) -> object:
            return self._value

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

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
            "inputs": dict(inputs)
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
        "tool_progress_context",
        lambda callback: nullcontext(),
    )

    executor = WorkflowExecutor(repository=object(), vault_provider=lambda: object())
    result = await executor._execute_graph(
        workflow_id=UUID(int=0),
        graph_config={"nodes": []},
        inputs={"message": "hello"},
        config={"configurable": {"thread_id": "thread"}},
        state_config={"configurable": {"thread_id": "thread"}},
        step_callback=lambda step: asyncio.sleep(0),
    )

    assert result["assistant_message"] == "What is your guess?"
    assert result["__interrupt__"] == [{"message": "What is your guess?"}]


@pytest.mark.asyncio
async def test_execute_graph_invoke_interrupt_fallback_does_not_leak_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``ainvoke`` interrupt fallback must not leak raw state as the reply.

    When ``snapshot.interrupts`` comes back empty alongside a truthy
    ``__interrupt__`` in the invoke result, the executor should fall back to the
    result's own ``__interrupt__`` list rather than the whole state mapping.
    """

    interrupt_payload = {"message": "Need your input"}

    class DummyCompiled:
        async def ainvoke(self, payload, *, config):
            return {
                "__interrupt__": ({"value": interrupt_payload},),
                "secret_state": "must-not-leak",
                "node_results": {"agent": {"branch": "human"}},
            }

        async def aget_state(self, config):
            # No interrupts surfaced on the snapshot -> exercise the fallback.
            return SimpleNamespace(values={"messages": []}, interrupts=())

    class DummyGraph:
        def compile(self, *, checkpointer, store):
            return DummyCompiled()

    class DummyAsyncContext:
        def __init__(self, value: object) -> None:
            self._value = value

        async def __aenter__(self) -> object:
            return self._value

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

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
            "inputs": dict(inputs)
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

    executor = WorkflowExecutor(repository=object(), vault_provider=lambda: object())
    result = await executor._execute_graph(
        workflow_id=UUID(int=0),
        graph_config={"nodes": []},
        inputs={"message": "hello"},
        config={"configurable": {"thread_id": "thread"}},
        state_config={"configurable": {"thread_id": "thread"}},
        step_callback=None,  # force the non-streaming ainvoke path
    )

    assert result["__interrupt__"] == [interrupt_payload]
    assert result["assistant_message"] == "Need your input"
    # The raw graph state must not have leaked into the interrupt payload.
    assert "secret_state" not in result["__interrupt__"][0]


@pytest.mark.asyncio
async def test_execute_graph_resumes_pending_interrupt_with_chatkit_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending checkpoint interrupt should be resumed with Command(resume=...)."""
    captured: dict[str, object] = {}

    class DummyInterrupt:
        value = {"message": "What is your guess?"}

    class DummyCompiled:
        def __init__(self) -> None:
            self._streamed = False

        async def astream(self, payload, *, config, stream_mode, subgraphs=False):
            captured["payload"] = payload
            self._streamed = True
            yield {"finish": {"assistant_message": "Correct."}}

        async def aget_state(self, config):
            if not self._streamed:
                return SimpleNamespace(
                    values={"messages": []}, interrupts=(DummyInterrupt(),)
                )
            return SimpleNamespace(
                values={"assistant_message": "Correct.", "messages": []},
                interrupts=(),
            )

    class DummyGraph:
        def compile(self, *, checkpointer, store):
            return DummyCompiled()

    class DummyAsyncContext:
        def __init__(self, value: object) -> None:
            self._value = value

        async def __aenter__(self) -> object:
            return self._value

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

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
        lambda *args, **kwargs: pytest.fail("resume must not build fresh state"),
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
        "tool_progress_context",
        lambda callback: nullcontext(),
    )

    executor = WorkflowExecutor(repository=object(), vault_provider=lambda: object())
    result = await executor._execute_graph(
        workflow_id=UUID(int=0),
        graph_config={"nodes": []},
        inputs={"message": "42"},
        config={"configurable": {"thread_id": "thread"}},
        state_config={"configurable": {"thread_id": "thread"}},
        step_callback=lambda step: asyncio.sleep(0),
    )

    payload = captured["payload"]
    assert isinstance(payload, Command)
    assert payload.resume == "42"
    assert result["assistant_message"] == "Correct."


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


@pytest.mark.asyncio
async def test_run_rejects_workspace_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """run should reject a workspace hint that does not match the repository."""

    workflow = SimpleNamespace(chatkit=None)
    version = SimpleNamespace(id=UUID(int=2), graph={"nodes": []}, runnable_config={})

    class Repository:
        async def get_workflow(self, workflow_id):
            return workflow

        async def get_latest_version(self, workflow_id):
            return version

        async def get_workflow_workspace_id(self, workflow_id):
            return "workspace-a"

    executor = WorkflowExecutor(
        repository=Repository(), vault_provider=lambda: object()
    )

    with pytest.raises(WorkflowNotFoundError):
        await executor.run(
            UUID(int=1),
            {"message": "hello"},
            workspace_id="workspace-b",
        )


@pytest.mark.asyncio
async def test_execute_graph_passes_workspace_id_to_initial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_execute_graph should forward the workspace id into build_initial_state."""

    captured: dict[str, object] = {}

    class DummyCompiled:
        async def ainvoke(self, payload, *, config):
            captured["payload"] = payload
            captured["config"] = config
            return {"reply": "done"}

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

    executor = WorkflowExecutor(repository=object(), vault_provider=lambda: object())

    result = await executor._execute_graph(
        workflow_id=UUID(int=0),
        graph_config={"nodes": []},
        inputs={"message": "hello"},
        config={"configurable": {"thread_id": "thread"}},
        state_config={"configurable": {"thread_id": "thread"}},
        step_callback=None,
        workspace_id=str(UUID(int=1)),
    )

    assert result == {"reply": "done"}
    assert captured["payload"] == {
        "inputs": {"message": "hello"},
        "workspace_id": str(UUID(int=1)),
    }


def test_with_request_inputs_non_mapping_configurable() -> None:
    """_with_request_inputs uses empty dict when configurable is not a Mapping (line 693)."""
    from orcheo_backend.app.chatkit.workflow_executor import _with_request_inputs

    # configurable is a plain string → not a Mapping → use {}
    result = _with_request_inputs(
        {"configurable": "not-a-mapping", "run_name": "test"},
        {"message": "hello"},
    )

    assert result["configurable"]["inputs"] == {"message": "hello"}
    assert result["run_name"] == "test"


def test_with_request_inputs_none_configurable() -> None:
    """_with_request_inputs handles absent configurable key (line 693)."""
    from orcheo_backend.app.chatkit.workflow_executor import _with_request_inputs

    result = _with_request_inputs({}, {"query": "q"})

    assert result["configurable"]["inputs"] == {"query": "q"}


@pytest.mark.asyncio
async def test_mark_run_succeeded_calls_repository() -> None:
    """_mark_run_succeeded should call repository.mark_run_succeeded with the reply."""
    from orcheo_backend.app.chatkit.workflow_executor import WorkflowExecutor

    calls: list[dict] = []

    class Repository:
        async def mark_run_succeeded(self, run_id, *, actor, output):
            calls.append({"run_id": run_id, "actor": actor, "output": output})

    run = SimpleNamespace(id="run-123")
    executor = WorkflowExecutor(
        repository=Repository(), vault_provider=lambda: object()
    )
    await executor._mark_run_succeeded(run, "admin", "The answer is 42")

    assert len(calls) == 1
    assert calls[0]["run_id"] == "run-123"
    assert calls[0]["actor"] == "admin"
    assert calls[0]["output"] == {"reply": "The answer is 42"}


@pytest.mark.asyncio
async def test_mark_run_succeeded_returns_early_when_run_is_none() -> None:
    """_mark_run_succeeded should be a no-op when run is None."""
    from orcheo_backend.app.chatkit.workflow_executor import WorkflowExecutor

    calls: list[dict] = []

    class Repository:
        async def mark_run_succeeded(self, run_id, *, actor, output):
            calls.append({})

    executor = WorkflowExecutor(
        repository=Repository(), vault_provider=lambda: object()
    )
    await executor._mark_run_succeeded(None, "admin", "reply text")

    assert calls == []


def test_build_reply_state_with_pydantic_base_model() -> None:
    """_build_reply_state should call model_dump() when state is a BaseModel."""
    from pydantic import BaseModel

    class FakeState(BaseModel):
        reply: str
        extra: str = "value"

    final_state = FakeState(reply="pydantic reply")
    reply, state_view = _build_reply_state(final_state)

    assert reply == "pydantic reply"
    assert state_view.get("extra") == "value"


# ---------------------------------------------------------------------------
# _checkpoint_message_count (workflow_executor.py line 291->295)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_message_count_returns_zero_for_non_mapping_values() -> None:
    """Covers line 291->295: snapshot.values is not a Mapping → returns 0."""

    class NonMappingSnapshot:
        values = "not-a-mapping"

    class CompiledWithState:
        async def aget_state(self, config: object) -> NonMappingSnapshot:
            return NonMappingSnapshot()

    result = await WorkflowExecutor._checkpoint_message_count(CompiledWithState(), {})
    assert result == 0


@pytest.mark.asyncio
async def test_checkpoint_message_count_returns_zero_when_no_aget_state() -> None:
    """_checkpoint_message_count returns 0 when compiled has no aget_state."""

    class CompiledWithoutState:
        pass

    result = await WorkflowExecutor._checkpoint_message_count(
        CompiledWithoutState(), {}
    )
    assert result == 0
