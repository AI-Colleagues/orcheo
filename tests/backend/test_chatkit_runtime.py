"""ChatKit runtime helpers."""

from __future__ import annotations
import asyncio
import sys
from types import SimpleNamespace
import pytest
from orcheo_backend.app import chatkit_runtime


def test_sensitive_logging_enabled_accepts_dev_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Development-like env vars enable sensitive logging."""
    monkeypatch.setenv("ORCHEO_ENV", "DEV")
    monkeypatch.delenv("NODE_ENV", raising=False)
    monkeypatch.delenv("ORCHEO_LOG_SENSITIVE_DEBUG", raising=False)

    assert chatkit_runtime.sensitive_logging_enabled() is True


def test_sensitive_logging_enabled_defaults_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sensitive logging stays disabled outside known environments."""
    monkeypatch.delenv("ORCHEO_ENV", raising=False)
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.delenv("ORCHEO_LOG_SENSITIVE_DEBUG", raising=False)

    assert chatkit_runtime.sensitive_logging_enabled() is False


def test_sensitive_logging_enabled_checks_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ORCHEO_LOG_SENSITIVE_DEBUG flag overrides non-dev environments."""
    monkeypatch.delenv("ORCHEO_ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)
    monkeypatch.setenv("ORCHEO_LOG_SENSITIVE_DEBUG", "1")

    assert chatkit_runtime.sensitive_logging_enabled() is True


def test_get_chatkit_server_initializes_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_chatkit_server creates a singleton server instance."""
    chatkit_runtime._chatkit_server_ref["server"] = None

    repository = object()
    created = []

    def fake_get_repository() -> object:
        return repository

    def fake_create_chatkit_server(repo_arg, vault_factory):  # type: ignore[no-untyped-def]
        assert repo_arg is repository
        assert vault_factory is chatkit_runtime.get_vault
        server = object()
        created.append(server)
        return server

    monkeypatch.setattr(chatkit_runtime, "get_repository", fake_get_repository)
    monkeypatch.setattr(
        chatkit_runtime, "create_chatkit_server", fake_create_chatkit_server
    )

    try:
        first_server = chatkit_runtime.get_chatkit_server()
        second_server = chatkit_runtime.get_chatkit_server()

        assert first_server is second_server
        assert created == [first_server]
    finally:
        chatkit_runtime._chatkit_server_ref["server"] = None


@pytest.mark.asyncio
async def test_chatkit_cleanup_task_handles_cancelled_prune(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup task should clear its state if pruning is cancelled."""

    class CancelingPostgresChatKitStore:
        async def prune_threads_older_than(self, *_: object, **__: object) -> int:
            raise asyncio.CancelledError

    store = CancelingPostgresChatKitStore()
    mock_server = type("Server", (), {"store": store})()
    chatkit_runtime._chatkit_server_ref["server"] = mock_server

    try:
        monkeypatch.setattr(
            chatkit_runtime,
            "PostgresChatKitStore",
            CancelingPostgresChatKitStore,
        )
        monkeypatch.setattr(chatkit_runtime, "_CHATKIT_CLEANUP_INTERVAL_SECONDS", 0.1)
        chatkit_runtime._chatkit_cleanup_task["task"] = None

        await chatkit_runtime.ensure_chatkit_cleanup_task()
        task = chatkit_runtime._chatkit_cleanup_task["task"]
        assert task is not None

        with pytest.raises(asyncio.CancelledError):
            await task

        assert chatkit_runtime._chatkit_cleanup_task["task"] is None
    finally:
        chatkit_runtime._chatkit_server_ref["server"] = None
        chatkit_runtime._chatkit_cleanup_task["task"] = None


@pytest.mark.asyncio
async def test_chatkit_cleanup_task_logs_orphan_pruning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero orphan prune count should reach the logging branch."""

    async def _prune_threads(*_args: object, **_kwargs: object) -> int:
        return 1

    async def _prune_orphans(*_args: object, **_kwargs: object) -> int:
        return 2

    sleep_calls = 0
    real_sleep = asyncio.sleep

    async def _sleep(*_args: object, **_kwargs: object) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        raise asyncio.CancelledError

    store = SimpleNamespace(
        prune_threads_older_than=_prune_threads,
        attachment_service=SimpleNamespace(
            prune_orphaned_upload_sessions=_prune_orphans
        ),
    )

    app_proxy = sys.modules["orcheo_backend.app"]
    monkeypatch.setattr(app_proxy, "_get_chatkit_store", lambda: store)
    monkeypatch.setattr(app_proxy, "_chatkit_retention_days", lambda: 1)
    monkeypatch.setattr(chatkit_runtime, "_CHATKIT_CLEANUP_INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(chatkit_runtime.asyncio, "sleep", _sleep)
    chatkit_runtime._chatkit_cleanup_task["task"] = None

    await chatkit_runtime.ensure_chatkit_cleanup_task()
    task = chatkit_runtime._chatkit_cleanup_task["task"]
    assert task is not None
    await real_sleep(0)
    assert task.done()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert sleep_calls == 1
    chatkit_runtime._chatkit_cleanup_task["task"] = None


@pytest.mark.asyncio
async def test_chatkit_cleanup_task_skips_orphan_logging_when_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero orphan prune count should skip the logging branch."""

    async def _prune_threads(*_args: object, **_kwargs: object) -> int:
        return 0

    async def _prune_orphans(*_args: object, **_kwargs: object) -> int:
        return 0

    sleep_calls = 0
    real_sleep = asyncio.sleep

    async def _sleep(*_args: object, **_kwargs: object) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        raise asyncio.CancelledError

    store = SimpleNamespace(
        prune_threads_older_than=_prune_threads,
        attachment_service=SimpleNamespace(
            prune_orphaned_upload_sessions=_prune_orphans
        ),
    )

    app_proxy = sys.modules["orcheo_backend.app"]
    monkeypatch.setattr(app_proxy, "_get_chatkit_store", lambda: store)
    monkeypatch.setattr(app_proxy, "_chatkit_retention_days", lambda: 1)
    monkeypatch.setattr(chatkit_runtime, "_CHATKIT_CLEANUP_INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(chatkit_runtime.asyncio, "sleep", _sleep)
    chatkit_runtime._chatkit_cleanup_task["task"] = None

    await chatkit_runtime.ensure_chatkit_cleanup_task()
    task = chatkit_runtime._chatkit_cleanup_task["task"]
    assert task is not None
    await real_sleep(0)

    with pytest.raises(asyncio.CancelledError):
        await task

    assert sleep_calls == 1
    chatkit_runtime._chatkit_cleanup_task["task"] = None
