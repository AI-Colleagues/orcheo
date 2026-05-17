"""Tests for ChatKit background maintenance and store helpers."""

from __future__ import annotations
import asyncio
import importlib
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest
from orcheo_backend.app import (
    _cancel_chatkit_cleanup_task,
    _chatkit_cleanup_task,
    _chatkit_server_ref,
    _ensure_chatkit_cleanup_task,
    _get_chatkit_store,
)
from orcheo_backend.app.chatkit import InMemoryChatKitStore

chatkit_runtime_module = importlib.import_module("orcheo_backend.app.chatkit_runtime")


class FakePostgresChatKitStore:
    def __init__(self) -> None:
        self.prune_calls = 0
        self.last_cutoff: datetime | None = None

    async def prune_threads_older_than(self, cutoff: datetime) -> int:
        self.prune_calls += 1
        self.last_cutoff = cutoff
        return 1


@pytest.mark.asyncio()
async def test_ensure_chatkit_cleanup_task_already_running() -> None:
    """Cleanup task should not be recreated if already running."""

    task = asyncio.create_task(asyncio.sleep(10))
    _chatkit_cleanup_task["task"] = task

    try:
        await _ensure_chatkit_cleanup_task()
        assert _chatkit_cleanup_task["task"] is task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        _chatkit_cleanup_task["task"] = None


@pytest.mark.asyncio()
async def test_cancel_chatkit_cleanup_task_no_task() -> None:
    """Canceling cleanup task when none exists should be safe."""

    _chatkit_cleanup_task["task"] = None
    await _cancel_chatkit_cleanup_task()
    assert _chatkit_cleanup_task["task"] is None


@pytest.mark.asyncio()
async def test_chatkit_cleanup_task_prunes_threads() -> None:
    """Cleanup task should prune old threads and log the count."""

    store = FakePostgresChatKitStore()
    mock_server = MagicMock()
    mock_server.store = store
    _chatkit_server_ref["server"] = mock_server

    with patch.object(
        chatkit_runtime_module, "PostgresChatKitStore", FakePostgresChatKitStore
    ):
        with (
            patch("orcheo_backend.app._chatkit_retention_days", return_value=30),
            patch("orcheo_backend.app._CHATKIT_CLEANUP_INTERVAL_SECONDS", 0.1),
        ):
            _chatkit_cleanup_task["task"] = None
            await _ensure_chatkit_cleanup_task()
            task = _chatkit_cleanup_task["task"]
            assert task is not None

            await asyncio.sleep(0.2)

            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    assert store.prune_calls >= 1
    assert store.last_cutoff is not None
    _chatkit_server_ref["server"] = None
    _chatkit_cleanup_task["task"] = None


def test_get_chatkit_store_returns_none_when_no_server() -> None:
    """Get chatkit store should return None when server is not initialized."""

    _chatkit_server_ref["server"] = None
    store = _get_chatkit_store()
    assert store is None


def test_get_chatkit_store_returns_none_for_non_postgres_store() -> None:
    """Get chatkit store should return None when store is not PostgresChatKitStore."""

    mock_server = MagicMock()
    mock_server.store = InMemoryChatKitStore()
    _chatkit_server_ref["server"] = mock_server

    try:
        store = _get_chatkit_store()
        assert store is None
    finally:
        _chatkit_server_ref["server"] = None
