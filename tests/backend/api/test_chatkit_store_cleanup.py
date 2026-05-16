"""ChatKit store retrieval and cleanup tests for orcheo_backend.app."""

from __future__ import annotations
import asyncio
import importlib
from unittest.mock import Mock, patch
import pytest
from tests.backend.api.shared import backend_app


chatkit_runtime_module = importlib.import_module("orcheo_backend.app.chatkit_runtime")


class FakePostgresChatKitStore:
    def __init__(self) -> None:
        self.prune_calls = 0

    async def prune_threads_older_than(self, *_: object, **__: object) -> int:
        self.prune_calls += 1
        return 0


def test_get_chatkit_store_when_no_server() -> None:
    """_get_chatkit_store returns None when server missing."""
    with patch.dict(backend_app._chatkit_server_ref, {"server": None}):
        result = backend_app._get_chatkit_store()
        assert result is None


def test_get_chatkit_store_when_not_postgres_store() -> None:
    """_get_chatkit_store ignores stores that are not PostgresChatKitStore."""
    mock_server = Mock()
    mock_server.store = Mock()
    with patch.dict(backend_app._chatkit_server_ref, {"server": mock_server}):
        result = backend_app._get_chatkit_store()
        assert result is None


def test_get_chatkit_store_when_no_store_attr() -> None:
    """_get_chatkit_store handles servers without store attribute."""
    mock_server = Mock(spec=[])
    with patch.dict(backend_app._chatkit_server_ref, {"server": mock_server}):
        result = backend_app._get_chatkit_store()
        assert result is None


@pytest.mark.asyncio
async def test_ensure_chatkit_cleanup_task_when_no_store() -> None:
    """_ensure_chatkit_cleanup_task skips when no store."""
    with patch.dict(backend_app._chatkit_cleanup_task, {"task": None}):
        with patch.object(backend_app, "_get_chatkit_store", return_value=None):
            await backend_app._ensure_chatkit_cleanup_task()
            assert backend_app._chatkit_cleanup_task["task"] is None


@pytest.mark.asyncio
async def test_cancel_chatkit_cleanup_task_when_no_task() -> None:
    """_cancel_chatkit_cleanup_task exits cleanly when nothing running."""
    with patch.dict(backend_app._chatkit_cleanup_task, {"task": None}):
        await backend_app._cancel_chatkit_cleanup_task()
        assert backend_app._chatkit_cleanup_task["task"] is None


@pytest.mark.asyncio
async def test_chatkit_cleanup_task_with_valid_store() -> None:
    """Cleanup task spins up when a valid Postgres store is available."""
    store = FakePostgresChatKitStore()
    mock_server = Mock()
    mock_server.store = store

    with patch.object(
        chatkit_runtime_module, "PostgresChatKitStore", FakePostgresChatKitStore
    ):
        with patch.dict(backend_app._chatkit_server_ref, {"server": mock_server}):
            with patch.dict(backend_app._chatkit_cleanup_task, {"task": None}):
                with patch.object(
                    backend_app, "_CHATKIT_CLEANUP_INTERVAL_SECONDS", 0.05
                ):
                    await backend_app._ensure_chatkit_cleanup_task()
                    task = backend_app._chatkit_cleanup_task["task"]
                    assert task is not None

                    await asyncio.sleep(0.15)

                    await backend_app._cancel_chatkit_cleanup_task()
                    assert backend_app._chatkit_cleanup_task["task"] is None

    assert store.prune_calls >= 1
