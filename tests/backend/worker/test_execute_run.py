"""Tests for the execute_run Celery task."""

from __future__ import annotations
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from orcheo_backend.app.external_agent_runtime_store import ExternalAgentRuntimeStore
from orcheo_backend.app.schemas.system import ExternalAgentProviderName


@pytest.fixture
def mock_run() -> MagicMock:
    """Create a mock pending run."""
    run = MagicMock()
    run.id = uuid4()
    run.workflow_version_id = uuid4()
    run.status = "pending"
    run.input_payload = {"test": "data"}
    run.runnable_config = None
    run.workspace_id = "workspace-1"
    return run


@pytest.fixture
def mock_version() -> MagicMock:
    """Create a mock workflow version."""
    version = MagicMock()
    version.id = uuid4()
    version.workflow_id = uuid4()
    version.graph = {"nodes": [], "edges": []}
    version.runnable_config = None
    return version


class TestLoadAndValidateRun:
    """Tests for _load_and_validate_run function."""

    @pytest.mark.asyncio
    async def test_run_not_found_returns_failed(self) -> None:
        """Test that missing run returns failed status."""
        from orcheo_backend.app.repository import WorkflowRunNotFoundError
        from orcheo_backend.worker.tasks import _load_and_validate_run

        mock_repo = MagicMock()
        mock_repo.get_run = AsyncMock(side_effect=WorkflowRunNotFoundError("not found"))

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            run, error = await _load_and_validate_run(str(uuid4()), "workspace-1")

        assert run is None
        assert error is not None
        assert error["status"] == "failed"
        assert "not found" in error["error"].lower()

    @pytest.mark.asyncio
    async def test_non_pending_run_is_skipped(self, mock_run: MagicMock) -> None:
        """Test that runs not in pending status are skipped."""
        from orcheo_backend.worker.tasks import _load_and_validate_run

        mock_run.status = "running"
        mock_repo = MagicMock()
        mock_repo.get_run = AsyncMock(return_value=mock_run)

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            run, error = await _load_and_validate_run(str(mock_run.id), "workspace-1")

        assert run is None
        assert error is not None
        assert error["status"] == "skipped"
        assert "running" in error["reason"]

    @pytest.mark.asyncio
    async def test_already_completed_run_is_skipped(self, mock_run: MagicMock) -> None:
        """Test that completed runs are skipped."""
        from orcheo_backend.worker.tasks import _load_and_validate_run

        mock_run.status = "succeeded"
        mock_repo = MagicMock()
        mock_repo.get_run = AsyncMock(return_value=mock_run)

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            run, error = await _load_and_validate_run(str(mock_run.id), "workspace-1")

        assert run is None
        assert error is not None
        assert error["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_pending_run_returns_run_object(self, mock_run: MagicMock) -> None:
        """Test that pending runs return the run object."""
        from orcheo_backend.worker.tasks import _load_and_validate_run

        mock_repo = MagicMock()
        mock_repo.get_run = AsyncMock(return_value=mock_run)

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            run, error = await _load_and_validate_run(str(mock_run.id), "workspace-1")

        assert run is mock_run
        assert error is None
        mock_repo.get_run.assert_awaited_once_with(
            mock_run.id, workspace_id="workspace-1"
        )

    @pytest.mark.asyncio
    async def test_missing_workspace_header_is_rejected(
        self, mock_run: MagicMock
    ) -> None:
        """Runs without workspace context should be rejected."""
        from orcheo_backend.worker.tasks import _load_and_validate_run

        mock_repo = MagicMock()

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            run, error = await _load_and_validate_run(str(mock_run.id), None)

        assert run is None
        assert error == {"status": "failed", "error": "Missing workspace_id header"}
        mock_repo.get_run.assert_not_called()


class TestMarkRunStarted:
    """Tests for _mark_run_started function."""

    @pytest.mark.asyncio
    async def test_mark_started_failure_returns_error(
        self, mock_run: MagicMock
    ) -> None:
        """Test that failure to mark as started returns error."""
        from orcheo_backend.worker.tasks import _mark_run_started

        mock_repo = MagicMock()
        mock_repo.mark_run_started = AsyncMock(
            side_effect=ValueError("already started")
        )

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            result = await _mark_run_started(mock_run, str(mock_run.id))

        assert result is not None
        assert result["status"] == "skipped"
        assert "already started" in result["reason"]

    @pytest.mark.asyncio
    async def test_mark_started_success_returns_none(self, mock_run: MagicMock) -> None:
        """Test that successful mark_started returns None."""
        from orcheo_backend.worker.tasks import _mark_run_started

        mock_repo = MagicMock()
        mock_repo.mark_run_started = AsyncMock(return_value=mock_run)

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            result = await _mark_run_started(mock_run, str(mock_run.id))

        assert result is None


class TestExecuteRunTask:
    """Tests for the execute_run Celery task."""

    def test_task_runs_async_function(self) -> None:
        """Test that the Celery task correctly calls the async function."""
        from orcheo_backend.worker.tasks import execute_run

        run_id = str(uuid4())
        mock_result: dict[str, Any] = {"status": "succeeded"}

        with patch("orcheo_backend.worker.tasks._get_event_loop") as mock_get_loop:
            mock_loop = MagicMock()
            # Return the expected value directly, bypassing async execution
            mock_loop.run_until_complete.return_value = mock_result
            mock_get_loop.return_value = mock_loop

            # Use new=MagicMock() to avoid AsyncMock creating unawaited coroutines
            with patch(
                "orcheo_backend.worker.tasks._execute_run_async",
                new=MagicMock(return_value=MagicMock()),
            ) as mock_execute_async:
                fake_self = SimpleNamespace(
                    request=SimpleNamespace(headers={"workspace_id": "workspace-1"})
                )
                result = execute_run.__wrapped__.__func__(fake_self, run_id)

        assert result == mock_result
        mock_loop.run_until_complete.assert_called_once()
        mock_execute_async.assert_called_once_with(run_id, "workspace-1")


class TestDispatchCronTriggers:
    """Tests for the dispatch_cron_triggers Celery task."""

    def test_dispatches_due_runs(self) -> None:
        """Test that cron dispatch enqueues runs."""
        from orcheo_backend.worker.tasks import dispatch_cron_triggers

        mock_run = MagicMock()
        mock_run.id = uuid4()
        expected_run_ids = [str(mock_run.id)]

        with patch("orcheo_backend.worker.tasks._get_event_loop") as mock_get_loop:
            mock_loop = MagicMock()
            # Return the expected value directly, bypassing async execution
            mock_loop.run_until_complete.return_value = expected_run_ids
            mock_get_loop.return_value = mock_loop

            # Use new=MagicMock() to avoid AsyncMock creating unawaited coroutines
            with patch(
                "orcheo_backend.worker.tasks._dispatch_cron_triggers_async",
                new=MagicMock(return_value=MagicMock()),
            ):
                result = dispatch_cron_triggers()

        assert "dispatched_runs" in result
        assert result["dispatched_runs"] == expected_run_ids


class TestExternalAgentTasks:
    """Tests for worker-side external agent helper tasks."""

    def test_external_agent_provider_environment_loads_from_runtime_store(self) -> None:
        """Worker tasks should load shared provider env vars from the runtime store."""
        from orcheo_backend.worker.tasks import _external_agent_provider_environment

        store = ExternalAgentRuntimeStore()
        store._redis = None
        store.save_provider_environment(
            ExternalAgentProviderName.CLAUDE_CODE,
            {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-shared"},
        )

        with patch(
            "orcheo_backend.app.dependencies.get_external_agent_runtime_store",
            return_value=store,
        ):
            environ = _external_agent_provider_environment()

        assert environ["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-shared"

    def test_patched_environment_temporarily_sets_provider_env(self) -> None:
        """Patched env should expose shared auth during a run and restore after."""
        from orcheo_backend.worker.tasks import _patched_environment

        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        with _patched_environment({"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-shared"}):
            assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-shared"

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ

    def test_refresh_external_agent_status_runs_async_helper(self) -> None:
        """The refresh task should delegate to the async helper via the event loop."""
        from orcheo_backend.worker.tasks import refresh_external_agent_status

        mock_result = {"status": "ready"}

        with patch("orcheo_backend.worker.tasks._get_event_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_loop.run_until_complete.return_value = mock_result
            mock_get_loop.return_value = mock_loop

            with patch(
                "orcheo_backend.worker.tasks._refresh_external_agent_status_async",
                new=MagicMock(return_value=MagicMock()),
            ):
                result = refresh_external_agent_status("codex")

        assert result == mock_result
        mock_loop.run_until_complete.assert_called_once()

    def test_start_external_agent_login_runs_async_helper(self) -> None:
        """The login task should delegate to the async helper via the event loop."""
        from orcheo_backend.worker.tasks import start_external_agent_login

        mock_result = {"status": "authenticated"}

        with patch("orcheo_backend.worker.tasks._get_event_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_loop.run_until_complete.return_value = mock_result
            mock_get_loop.return_value = mock_loop

            with patch(
                "orcheo_backend.worker.tasks._start_external_agent_login_async",
                new=MagicMock(return_value=MagicMock()),
            ):
                result = start_external_agent_login("codex", "session-1")

        assert result == mock_result
        mock_loop.run_until_complete.assert_called_once()

    def test_disconnect_external_agent_runs_async_helper(self) -> None:
        """The disconnect task should delegate to the async helper via the event loop."""  # noqa: E501
        from orcheo_backend.worker.tasks import disconnect_external_agent

        mock_result = {"status": "needs_login"}

        with patch("orcheo_backend.worker.tasks._get_event_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_loop.run_until_complete.return_value = mock_result
            mock_get_loop.return_value = mock_loop

            with patch(
                "orcheo_backend.worker.tasks._disconnect_external_agent_async",
                new=MagicMock(return_value=MagicMock()),
            ):
                result = disconnect_external_agent("codex")

        assert result == mock_result
        mock_loop.run_until_complete.assert_called_once()
