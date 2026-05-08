"""Tests for event loop handling in tasks.py."""

from __future__ import annotations
import asyncio
import os
from unittest.mock import patch
import pytest


class TestGetEventLoop:
    """Tests for _get_event_loop function."""

    def test_returns_existing_open_loop(self) -> None:
        """Test that existing open event loop is returned."""
        from orcheo_backend.worker.tasks import _get_event_loop

        existing_loop = asyncio.new_event_loop()
        try:
            with patch("asyncio.get_event_loop", return_value=existing_loop):
                loop = _get_event_loop()
                assert loop is existing_loop
        finally:
            existing_loop.close()

    def test_creates_new_loop_when_closed(self) -> None:
        """Test that new loop is created when existing loop is closed."""
        from orcheo_backend.worker.tasks import _get_event_loop

        closed_loop = asyncio.new_event_loop()
        closed_loop.close()
        new_loop = asyncio.new_event_loop()

        with patch("asyncio.get_event_loop", return_value=closed_loop):
            with patch("asyncio.new_event_loop", return_value=new_loop):
                with patch("asyncio.set_event_loop") as mock_set:
                    loop = _get_event_loop()

                    assert loop is new_loop
                    mock_set.assert_called_once_with(new_loop)

        new_loop.close()

    def test_creates_new_loop_on_runtime_error(self) -> None:
        """Test that new loop is created when get_event_loop raises RuntimeError."""
        from orcheo_backend.worker.tasks import _get_event_loop

        new_loop = asyncio.new_event_loop()

        def raise_runtime_error() -> None:
            raise RuntimeError("No running event loop")

        with patch("asyncio.get_event_loop", side_effect=raise_runtime_error):
            with patch("asyncio.new_event_loop", return_value=new_loop):
                with patch("asyncio.set_event_loop") as mock_set:
                    loop = _get_event_loop()

                    assert loop is new_loop
                    mock_set.assert_called_once_with(new_loop)

        new_loop.close()

    def test_loop_is_set_when_created(self) -> None:
        """Test that set_event_loop is called when creating new loop."""
        from orcheo_backend.worker.tasks import _get_event_loop

        new_loop = asyncio.new_event_loop()

        with patch("asyncio.get_event_loop", side_effect=RuntimeError("No loop")):
            with patch("asyncio.new_event_loop", return_value=new_loop):
                with patch("asyncio.set_event_loop") as mock_set:
                    _get_event_loop()
                    mock_set.assert_called_once_with(new_loop)

        new_loop.close()


def test_patched_environment_restores_existing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.worker.tasks import _patched_environment

    key = "ORCHEO_TASKS_TEST_ENV"
    monkeypatch.setenv(key, "original")

    with _patched_environment({key: "override"}):
        assert os.environ[key] == "override"

    assert os.environ[key] == "original"


@pytest.mark.asyncio
async def test_refresh_external_agent_status_async_proxies_to_worker_helper() -> None:
    from orcheo_backend.worker.tasks import _refresh_external_agent_status_async

    with patch(
        "orcheo_backend.worker.external_agents.refresh_external_agent_status_async",
        return_value={"status": "ready"},
    ) as refresh:
        result = await _refresh_external_agent_status_async("codex")

    refresh.assert_awaited_once_with("codex")
    assert result == {"status": "ready"}


@pytest.mark.asyncio
async def test_start_external_agent_login_async_proxies_to_worker_helper() -> None:
    from orcheo_backend.worker.tasks import _start_external_agent_login_async

    with patch(
        "orcheo_backend.worker.external_agents.start_external_agent_login_async",
        return_value={"status": "authenticated"},
    ) as start:
        result = await _start_external_agent_login_async("codex", "session-1")

    start.assert_awaited_once_with("codex", "session-1")
    assert result == {"status": "authenticated"}


@pytest.mark.asyncio
async def test_disconnect_external_agent_async_proxies_to_worker_helper() -> None:
    from orcheo_backend.worker.tasks import _disconnect_external_agent_async

    with patch(
        "orcheo_backend.worker.external_agents.disconnect_external_agent_async",
        return_value={"status": "needs_login"},
    ) as disconnect:
        result = await _disconnect_external_agent_async("gemini")

    disconnect.assert_awaited_once_with("gemini")
    assert result == {"status": "needs_login"}


@pytest.mark.asyncio
async def test_refresh_external_agent_status_async_with_workspace_id() -> None:
    """Line 529: workspace_id is not None path in _refresh_external_agent_status_async."""
    from orcheo_backend.worker.tasks import _refresh_external_agent_status_async

    with patch(
        "orcheo_backend.worker.external_agents.refresh_external_agent_status_async",
        return_value={"status": "ready"},
    ) as refresh:
        result = await _refresh_external_agent_status_async(
            "codex", workspace_id="ws-1"
        )

    refresh.assert_awaited_once_with("codex", workspace_id="ws-1")
    assert result == {"status": "ready"}


@pytest.mark.asyncio
async def test_start_external_agent_login_async_with_workspace_id() -> None:
    """Line 547: workspace_id is not None path in _start_external_agent_login_async."""
    from orcheo_backend.worker.tasks import _start_external_agent_login_async

    with patch(
        "orcheo_backend.worker.external_agents.start_external_agent_login_async",
        return_value={"status": "authenticated"},
    ) as start:
        result = await _start_external_agent_login_async(
            "codex", "sess-1", workspace_id="ws-1"
        )

    start.assert_awaited_once_with("codex", "sess-1", workspace_id="ws-1")
    assert result == {"status": "authenticated"}


@pytest.mark.asyncio
async def test_disconnect_external_agent_async_with_workspace_id() -> None:
    """Line 563: workspace_id is not None path in _disconnect_external_agent_async."""
    from orcheo_backend.worker.tasks import _disconnect_external_agent_async

    with patch(
        "orcheo_backend.worker.external_agents.disconnect_external_agent_async",
        return_value={"status": "needs_login"},
    ) as disconnect:
        result = await _disconnect_external_agent_async("gemini", workspace_id="ws-1")

    disconnect.assert_awaited_once_with("gemini", workspace_id="ws-1")
    assert result == {"status": "needs_login"}


def test_refresh_external_agent_status_task_with_workspace_id() -> None:
    """Line 601: Celery task refresh_external_agent_status takes workspace_id branch."""
    import asyncio
    from orcheo_backend.worker import tasks as tasks_mod

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def fake_refresh(name: str, workspace_id: str | None = None) -> dict:
        return {"status": "ready", "workspace_id": workspace_id}

    try:
        with (
            patch.object(tasks_mod, "_get_event_loop", return_value=loop),
            patch.object(
                tasks_mod,
                "_refresh_external_agent_status_async",
                side_effect=fake_refresh,
            ),
        ):
            result = tasks_mod.refresh_external_agent_status.run(
                tasks_mod.refresh_external_agent_status, "codex", workspace_id="ws-1"
            )
    finally:
        loop.close()

    assert result["status"] == "ready"
    assert result["workspace_id"] == "ws-1"


def test_start_external_agent_login_task_with_workspace_id() -> None:
    """Line 624: Celery task start_external_agent_login takes workspace_id branch."""
    import asyncio
    from orcheo_backend.worker import tasks as tasks_mod

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def fake_start(
        name: str, session_id: str, workspace_id: str | None = None
    ) -> dict:
        return {"status": "authenticated", "workspace_id": workspace_id}

    try:
        with (
            patch.object(tasks_mod, "_get_event_loop", return_value=loop),
            patch.object(
                tasks_mod, "_start_external_agent_login_async", side_effect=fake_start
            ),
        ):
            result = tasks_mod.start_external_agent_login.run(
                tasks_mod.start_external_agent_login,
                "codex",
                "sess-1",
                workspace_id="ws-1",
            )
    finally:
        loop.close()

    assert result["status"] == "authenticated"
    assert result["workspace_id"] == "ws-1"


def test_disconnect_external_agent_task_with_workspace_id() -> None:
    """Line 640: Celery task disconnect_external_agent takes workspace_id branch."""
    import asyncio
    from orcheo_backend.worker import tasks as tasks_mod

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def fake_disconnect(name: str, workspace_id: str | None = None) -> dict:
        return {"status": "needs_login", "workspace_id": workspace_id}

    try:
        with (
            patch.object(tasks_mod, "_get_event_loop", return_value=loop),
            patch.object(
                tasks_mod,
                "_disconnect_external_agent_async",
                side_effect=fake_disconnect,
            ),
        ):
            result = tasks_mod.disconnect_external_agent.run(
                tasks_mod.disconnect_external_agent, "codex", workspace_id="ws-1"
            )
    finally:
        loop.close()

    assert result["status"] == "needs_login"
    assert result["workspace_id"] == "ws-1"
