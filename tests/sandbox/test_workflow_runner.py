"""Tests for the in-sandbox workflow runner module."""

from __future__ import annotations
import os
from collections.abc import Mapping
from typing import Any
import pytest


# ---------------------------------------------------------------------------
# _BrokerCredentialResolver tests (lines 67->69, 79-83, 86-90)
# ---------------------------------------------------------------------------


def test_broker_resolver_raises_for_unsupported_payload_path() -> None:
    """resolve() raises UnknownCredentialPayloadError for non-secret paths (lines 67->69)."""
    from orcheo.runtime.credentials import UnknownCredentialPayloadError
    from orcheo.runtime.credentials.references import CredentialReference
    from orcheo.sandbox.workflow_runner import _BrokerCredentialResolver

    resolver = _BrokerCredentialResolver(
        broker_url="http://broker",
        broker_token="tok",
        run_id="run-1",
        workspace_id="ws-1",
    )
    ref = CredentialReference(identifier="openai_api_key", payload_path=("metadata",))
    with pytest.raises(UnknownCredentialPayloadError, match="metadata"):
        resolver.resolve(ref)


def test_broker_resolver_sends_request_without_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve() omits X-Orcheo-Workspace when workspace_id is None (lines 79-83)."""
    import httpx
    from orcheo.runtime.credentials.references import CredentialReference
    from orcheo.sandbox.workflow_runner import _BrokerCredentialResolver

    sent_headers: list[dict[str, str]] = []

    class _FakeClient:
        @staticmethod
        def post(
            url: str, *, json: Any, headers: Any, timeout: float
        ) -> httpx.Response:
            sent_headers.append(dict(headers))
            return httpx.Response(200, json={"value": "secret-value"})

    monkeypatch.setattr(
        "orcheo.sandbox.workflow_runner.httpx",
        type("httpx", (), {"post": staticmethod(_FakeClient.post)})(),
    )

    resolver = _BrokerCredentialResolver(
        broker_url="http://broker",
        broker_token="tok",
        run_id="run-1",
        workspace_id=None,
    )
    ref = CredentialReference(identifier="openai_api_key", payload_path=())
    value = resolver.resolve(ref)

    assert value == "secret-value"
    assert len(sent_headers) == 1
    assert "X-Orcheo-Workspace" not in sent_headers[0]
    assert sent_headers[0]["Authorization"] == "Bearer tok"


def test_broker_resolver_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve() raises RuntimeError when the broker returns a non-success status (lines 86-90)."""
    import httpx
    from orcheo.runtime.credentials.references import CredentialReference
    from orcheo.sandbox.workflow_runner import _BrokerCredentialResolver

    class _FailClient:
        @staticmethod
        def post(
            url: str, *, json: Any, headers: Any, timeout: float
        ) -> httpx.Response:
            return httpx.Response(401, text="Unauthorized")

    monkeypatch.setattr(
        "orcheo.sandbox.workflow_runner.httpx",
        type("httpx", (), {"post": staticmethod(_FailClient.post)})(),
    )

    resolver = _BrokerCredentialResolver(
        broker_url="http://broker",
        broker_token="bad-token",
        run_id="run-1",
        workspace_id="ws-1",
    )
    ref = CredentialReference(identifier="openai_api_key", payload_path=("secret",))
    with pytest.raises(RuntimeError, match="401"):
        resolver.resolve(ref)


def test_broker_resolver_raises_when_value_is_not_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve() raises RuntimeError when broker response value is not a string."""
    import httpx
    from orcheo.runtime.credentials.references import CredentialReference
    from orcheo.sandbox.workflow_runner import _BrokerCredentialResolver

    class _WrongTypeClient:
        @staticmethod
        def post(
            url: str, *, json: Any, headers: Any, timeout: float
        ) -> httpx.Response:
            return httpx.Response(200, json={"value": 12345})

    monkeypatch.setattr(
        "orcheo.sandbox.workflow_runner.httpx",
        type("httpx", (), {"post": staticmethod(_WrongTypeClient.post)})(),
    )

    resolver = _BrokerCredentialResolver(
        broker_url="http://broker",
        broker_token="tok",
        run_id="run-1",
        workspace_id="ws-1",
    )
    ref = CredentialReference(identifier="openai_api_key", payload_path=())
    with pytest.raises(RuntimeError, match="invalid value"):
        resolver.resolve(ref)


# ---------------------------------------------------------------------------
# _credential_context tests (lines 105-106)
# ---------------------------------------------------------------------------


def test_credential_context_raises_when_broker_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_credential_context raises RuntimeError when ORCHEO_CREDENTIAL_BROKER_URL is unset (lines 105-106)."""
    from orcheo.sandbox.workflow_runner import _credential_context

    monkeypatch.setenv("ORCHEO_BROKER_TOKEN", "some-token")
    monkeypatch.delenv("ORCHEO_CREDENTIAL_BROKER_URL", raising=False)

    with pytest.raises(RuntimeError, match="ORCHEO_CREDENTIAL_BROKER_URL"):
        _credential_context(run_id="r1", workspace_id="ws-1")


def test_credential_context_returns_nullcontext_when_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_credential_context returns nullcontext() when no broker token is set."""
    from contextlib import nullcontext
    from orcheo.sandbox.workflow_runner import _credential_context

    monkeypatch.delenv("ORCHEO_BROKER_TOKEN", raising=False)
    ctx = _credential_context(run_id="r1", workspace_id="ws-1")
    # nullcontext is the expected type when no token is available.
    assert isinstance(ctx, type(nullcontext()))


# ---------------------------------------------------------------------------
# run_in_subprocess (lines 270-285) — empty queue path
# ---------------------------------------------------------------------------


def test_run_in_subprocess_handles_empty_queue_on_child_exit() -> None:
    """run_in_subprocess returns a failed result when the child process puts nothing (lines 270-285)."""
    from orcheo.sandbox.workflow_runner import run_in_subprocess

    # spawn=True with a definition that causes the child to crash before putting anything.
    # We achieve this by making the child import fail — inject a stub _run_graph that
    # forces an OOM-like immediate crash by killing the child process.

    # Use spawn=False and simulate the Empty path by directly exercising the fallback:
    # When spawn=True but the queue is empty we simulate by calling the private helper.
    import multiprocessing as mp
    from queue import Empty
    from orcheo.sandbox.workflow_runner import run_in_subprocess

    # Patch the mp.Process to simulate a killed child (exitcode < 0).
    class _FakeProcess:
        exitcode: int | None = -9

        def start(self) -> None:
            pass

        def join(self) -> None:
            pass

    class _EmptyQueue:
        def get_nowait(self) -> Any:
            raise Empty()

    original_get_context = mp.get_context

    def _fake_get_context(method: str) -> Any:
        ctx = original_get_context(method)

        class _PatchedCtx:
            def Queue(self) -> _EmptyQueue:
                return _EmptyQueue()  # type: ignore[return-value]

            def Process(self, *, target: Any, args: Any, daemon: bool) -> _FakeProcess:
                return _FakeProcess()

        return _PatchedCtx()

    import orcheo.sandbox.workflow_runner as runner_module

    original_ctx_fn = runner_module.mp.get_context  # type: ignore[attr-defined]
    runner_module.mp.get_context = _fake_get_context  # type: ignore[attr-defined]
    try:
        result = run_in_subprocess({}, {})
    finally:
        runner_module.mp.get_context = original_ctx_fn  # type: ignore[attr-defined]

    assert result["status"] == "failed"
    assert "signal" in str(result.get("error", "")).lower()


def test_run_in_subprocess_handles_clean_exit_with_empty_queue() -> None:
    """run_in_subprocess surfaces 'exited cleanly without result' for exitcode=0 (lines 270-285)."""
    import multiprocessing as mp
    from queue import Empty
    from orcheo.sandbox.workflow_runner import run_in_subprocess

    class _ZeroExitProcess:
        exitcode: int | None = 0

        def start(self) -> None:
            pass

        def join(self) -> None:
            pass

    class _EmptyQueue:
        def get_nowait(self) -> Any:
            raise Empty()

    original_get_context = mp.get_context

    def _fake_get_context(method: str) -> Any:
        class _PatchedCtx:
            def Queue(self) -> _EmptyQueue:
                return _EmptyQueue()  # type: ignore[return-value]

            def Process(
                self, *, target: Any, args: Any, daemon: bool
            ) -> _ZeroExitProcess:
                return _ZeroExitProcess()

        return _PatchedCtx()

    import orcheo.sandbox.workflow_runner as runner_module

    original_ctx_fn = runner_module.mp.get_context  # type: ignore[attr-defined]
    runner_module.mp.get_context = _fake_get_context  # type: ignore[attr-defined]
    try:
        result = run_in_subprocess({}, {})
    finally:
        runner_module.mp.get_context = original_ctx_fn  # type: ignore[attr-defined]

    assert result["status"] == "failed"
    assert "cleanly" in str(result.get("error", ""))


def test_run_in_subprocess_handles_nonzero_exit_with_empty_queue() -> None:
    """run_in_subprocess surfaces exit status when queue is empty and exitcode > 0 (lines 270-285)."""
    import multiprocessing as mp
    from queue import Empty
    from orcheo.sandbox.workflow_runner import run_in_subprocess

    class _NonzeroExitProcess:
        exitcode: int | None = 42

        def start(self) -> None:
            pass

        def join(self) -> None:
            pass

    class _EmptyQueue:
        def get_nowait(self) -> Any:
            raise Empty()

    original_get_context = mp.get_context

    def _fake_get_context(method: str) -> Any:
        class _PatchedCtx:
            def Queue(self) -> _EmptyQueue:
                return _EmptyQueue()  # type: ignore[return-value]

            def Process(
                self, *, target: Any, args: Any, daemon: bool
            ) -> _NonzeroExitProcess:
                return _NonzeroExitProcess()

        return _PatchedCtx()

    import orcheo.sandbox.workflow_runner as runner_module

    original_ctx_fn = runner_module.mp.get_context  # type: ignore[attr-defined]
    runner_module.mp.get_context = _fake_get_context  # type: ignore[attr-defined]
    try:
        result = run_in_subprocess({}, {})
    finally:
        runner_module.mp.get_context = original_ctx_fn  # type: ignore[attr-defined]

    assert result["status"] == "failed"
    assert "status 42" in str(result.get("error", ""))


def test_run_in_subprocess_handles_still_running_child() -> None:
    """run_in_subprocess surfaces 'still running' when exitcode is None (lines 270-285)."""
    import multiprocessing as mp
    from queue import Empty
    from orcheo.sandbox.workflow_runner import run_in_subprocess

    class _StillRunningProcess:
        exitcode: int | None = None

        def start(self) -> None:
            pass

        def join(self) -> None:
            pass

    class _EmptyQueue:
        def get_nowait(self) -> Any:
            raise Empty()

    original_get_context = mp.get_context

    def _fake_get_context(method: str) -> Any:
        class _PatchedCtx:
            def Queue(self) -> _EmptyQueue:
                return _EmptyQueue()  # type: ignore[return-value]

            def Process(
                self, *, target: Any, args: Any, daemon: bool
            ) -> _StillRunningProcess:
                return _StillRunningProcess()

        return _PatchedCtx()

    import orcheo.sandbox.workflow_runner as runner_module

    original_ctx_fn = runner_module.mp.get_context  # type: ignore[attr-defined]
    runner_module.mp.get_context = _fake_get_context  # type: ignore[attr-defined]
    try:
        result = run_in_subprocess({}, {})
    finally:
        runner_module.mp.get_context = original_ctx_fn  # type: ignore[attr-defined]

    assert result["status"] == "failed"
    assert "still running" in str(result.get("error", ""))


# ---------------------------------------------------------------------------
# _json_default (lines 295, 301-309)
# ---------------------------------------------------------------------------


def test_json_default_calls_model_dump_without_mode_when_type_error() -> None:
    """_json_default falls back to model_dump() without 'mode' on TypeError (line 295)."""
    from orcheo.sandbox.workflow_runner import _json_default

    class _PydanticV1Like:
        def model_dump(self, *, mode: str | None = None) -> dict[str, Any]:
            if mode is not None:
                raise TypeError("unexpected keyword argument 'mode'")
            return {"v1": "data"}

    result = _json_default(_PydanticV1Like())
    assert result == {"v1": "data"}


def test_json_default_serializes_bytes() -> None:
    """_json_default decodes bytes to string (lines 301-303)."""
    from orcheo.sandbox.workflow_runner import _json_default

    assert _json_default(b"hello") == "hello"
    assert _json_default(b"\xff\xfe") == "��"  # replacement chars


def test_json_default_serializes_set() -> None:
    """_json_default converts set to list (lines 304-306)."""
    from orcheo.sandbox.workflow_runner import _json_default

    result = _json_default({1, 2, 3})
    assert isinstance(result, list)
    assert sorted(result) == [1, 2, 3]


def test_json_default_serializes_frozenset() -> None:
    """_json_default converts frozenset to list (lines 304-306)."""
    from orcheo.sandbox.workflow_runner import _json_default

    result = _json_default(frozenset(["a", "b"]))
    assert isinstance(result, list)
    assert sorted(result) == ["a", "b"]


def test_json_default_falls_back_to_str() -> None:
    """_json_default falls back to str() for unrecognised types (lines 307-309)."""
    from orcheo.sandbox.workflow_runner import _json_default

    class _Opaque:
        def __repr__(self) -> str:
            return "opaque-object"

        def __str__(self) -> str:
            return "opaque-str"

    result = _json_default(_Opaque())
    assert result == "opaque-str"


@pytest.mark.asyncio
async def test_sandbox_thread_state_store_round_trips_payload(
    tmp_path: Path,
) -> None:
    """The thread-state store round-trips a payload through the backing file."""
    from orcheo.sandbox.workflow_runner import _SandboxThreadStateStore

    store = _SandboxThreadStateStore(tmp_path / "thread-state.json")
    namespace = ("ws-1", "insight_analyst", "thread-1")
    payload = {"draft_codebook": {"themes": []}}

    await store.aput(namespace, "state", payload)
    item = await store.aget(namespace, "state")

    assert item == {"value": payload}


@pytest.mark.asyncio
async def test_sandbox_thread_state_store_persists_across_instances(
    tmp_path: Path,
) -> None:
    """A new store instance can read state written by a previous instance."""
    from orcheo.sandbox.workflow_runner import _SandboxThreadStateStore

    path = tmp_path / "thread-state.json"
    namespace = ("ws-1", "insight_analyst", "thread-1")
    payload = {"approved_codebook": {"themes": [{"theme_id": "T01"}]}}

    first_store = _SandboxThreadStateStore(path)
    second_store = _SandboxThreadStateStore(path)

    await first_store.aput(namespace, "state", payload)
    item = await second_store.aget(namespace, "state")

    assert item == {"value": payload}


@pytest.mark.asyncio
async def test_sandbox_thread_state_store_returns_none_when_key_missing(
    tmp_path: Path,
) -> None:
    """aget returns None when key is not present in the namespace (line 177)."""
    from orcheo.sandbox.workflow_runner import _SandboxThreadStateStore

    store = _SandboxThreadStateStore(tmp_path / "thread-state-miss.json")
    namespace = ("ws-1", "insight_analyst", "thread-1")

    # Nothing stored → key absent → None (line 177)
    result = await store.aget(namespace, "nonexistent-key")

    assert result is None


@pytest.mark.asyncio
async def test_sandbox_thread_state_store_returns_none_for_missing_namespace(
    tmp_path: Path,
) -> None:
    """aget returns None when the namespace does not exist at all (line 177)."""
    from orcheo.sandbox.workflow_runner import _SandboxThreadStateStore

    store = _SandboxThreadStateStore(tmp_path / "state-ns.json")
    await store.aput(("ns-a",), "k", "v")

    result = await store.aget(("ns-b",), "k")  # different namespace

    assert result is None


def test_sandbox_thread_state_store_read_payload_handles_json_error(
    tmp_path: Path,
) -> None:
    """_read_payload returns {} when the file contains invalid JSON (lines 147-148)."""
    from orcheo.sandbox.workflow_runner import _SandboxThreadStateStore

    path = tmp_path / "bad.json"
    path.write_text("not valid json {{{{", encoding="utf-8")

    store = _SandboxThreadStateStore(path)
    payload = store._read_payload()

    assert payload == {}


def test_sandbox_thread_state_store_read_payload_handles_non_dict_json(
    tmp_path: Path,
) -> None:
    """_read_payload returns {} when JSON root is not a dict (line 155)."""
    import json
    from orcheo.sandbox.workflow_runner import _SandboxThreadStateStore

    path = tmp_path / "list.json"
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

    store = _SandboxThreadStateStore(path)
    payload = store._read_payload()

    assert payload == {}


def test_credential_context_returns_resolver_when_both_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_credential_context returns credential_resolution context when token+URL present (line 206)."""
    from orcheo.sandbox.workflow_runner import _credential_context

    monkeypatch.setenv("ORCHEO_BROKER_TOKEN", "tok-123")
    monkeypatch.setenv("ORCHEO_CREDENTIAL_BROKER_URL", "http://broker:9091")

    ctx = _credential_context(run_id="run-1", workspace_id="ws-1")

    # It should be a non-nullcontext context manager
    from contextlib import nullcontext

    assert not isinstance(ctx, type(nullcontext()))


def test_run_in_subprocess_no_spawn_success() -> None:
    """run_in_subprocess with spawn=False executes in-process and returns result (lines 336-351)."""
    from orcheo.sandbox.workflow_runner import run_in_subprocess
    from unittest.mock import patch

    def _fake_run_graph(
        workflow_def,
        inputs,
        *,
        runnable_config=None,
        state_config=None,
        run_id=None,
        workspace_id=None,
    ):
        return {"output": "hello"}

    with patch(
        "orcheo.sandbox.workflow_runner._run_graph", side_effect=_fake_run_graph
    ):
        result = run_in_subprocess(
            {"nodes": []},
            {"message": "hi"},
            spawn=False,
        )

    assert result["status"] == "succeeded"
    assert result["outputs"]["output"] == "hello"


def test_run_in_subprocess_no_spawn_failure() -> None:
    """run_in_subprocess with spawn=False handles _run_graph exception (lines 229-256)."""
    from orcheo.sandbox.workflow_runner import run_in_subprocess
    from unittest.mock import patch

    with patch(
        "orcheo.sandbox.workflow_runner._run_graph",
        side_effect=ValueError("graph error"),
    ):
        result = run_in_subprocess(
            {"nodes": []},
            {},
            spawn=False,
        )

    assert result["status"] == "failed"
    assert "graph error" in str(result.get("error", ""))
    assert "ValueError" in str(result.get("error", ""))


def test_json_default_serializes_dataclass() -> None:
    """_json_default returns asdict() for a real dataclass (line 295)."""
    from dataclasses import dataclass
    from orcheo.sandbox.workflow_runner import _json_default

    @dataclass
    class _Point:
        x: int
        y: int

    result = _json_default(_Point(x=3, y=7))
    assert result == {"x": 3, "y": 7}


def test_run_graph_hydrates_attachment_runtime_config(monkeypatch):
    """_run_graph restores sandbox-safe attachment descriptors before invoke."""
    from orcheo.runtime.attachments import (
        AttachmentScopeRecord,
        ChatKitAttachmentResolverProxy,
    )
    from orcheo.sandbox.workflow_runner import _run_graph

    captured: dict[str, Any] = {}

    class _Compiled:
        async def ainvoke(self, state: Mapping[str, Any], config: Mapping[str, Any]):
            captured["state_configurable"] = dict(
                state.get("config", {}).get("configurable", {})
            )
            captured["runtime_configurable"] = dict(config.get("configurable", {}))
            return {"reply": "ok"}

    class _Graph:
        def compile(self, **kwargs: object) -> _Compiled:
            captured["compile_kwargs"] = kwargs
            return _Compiled()

    monkeypatch.setattr(
        "orcheo.graph.builder.build_graph",
        lambda graph: _Graph(),
    )

    result = _run_graph(
        {
            "format": "graph",
        },
        {"message": "hello"},
        runnable_config={
            "configurable": {
                "attachment_resolver": {
                    "__orcheo_attachment_resolver__": {
                        "base_url": "https://api.example.com"
                    }
                },
                "attachment_scope": {
                    "__orcheo_attachment_scope__": {
                        "workspace_id": "ws-1",
                        "workflow_id": "wf-1",
                        "thread_id": "thr-1",
                        "upload_session_id": "ups-1",
                    }
                },
            }
        },
        state_config={
            "configurable": {
                "attachment_resolver": {
                    "__orcheo_attachment_resolver__": {
                        "base_url": "https://api.example.com"
                    }
                },
                "attachment_scope": {
                    "__orcheo_attachment_scope__": {
                        "workspace_id": "ws-1",
                        "workflow_id": "wf-1",
                        "thread_id": "thr-1",
                        "upload_session_id": "ups-1",
                    }
                },
            }
        },
        run_id="run-1",
        workspace_id="ws-1",
    )

    assert result == {"reply": "ok"}
    assert "store" in captured["compile_kwargs"]
    assert isinstance(
        captured["state_configurable"]["attachment_resolver"],
        ChatKitAttachmentResolverProxy,
    )
    assert isinstance(
        captured["state_configurable"]["attachment_scope"],
        AttachmentScopeRecord,
    )
    assert isinstance(
        captured["runtime_configurable"]["attachment_resolver"],
        ChatKitAttachmentResolverProxy,
    )
