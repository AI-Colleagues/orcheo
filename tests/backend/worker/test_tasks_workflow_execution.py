"""Tests for workflow execution functions in tasks.py."""

from __future__ import annotations
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from pydantic import BaseModel


@pytest.fixture
def mock_run() -> MagicMock:
    """Create a mock pending run."""
    run = MagicMock()
    run.id = uuid4()
    run.workflow_version_id = uuid4()
    run.status = MagicMock()
    run.status.value = "pending"
    run.input_payload = {"test": "data"}
    run.runnable_config = None
    run.tenant_id = "tenant-1"
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


class TestExtractOutput:
    """Tests for _extract_output function."""

    def test_extracts_dict_output(self) -> None:
        """Test that dict final state is wrapped correctly."""
        from orcheo_backend.worker.tasks import _extract_output

        final_state = {"key": "value", "count": 42}
        result = _extract_output(final_state)

        assert result == {"final_state": final_state}

    def test_extracts_pydantic_model_output(self) -> None:
        """Test that Pydantic model is dumped correctly."""
        from orcheo_backend.worker.tasks import _extract_output

        class SampleModel(BaseModel):
            name: str
            value: int

        model = SampleModel(name="test", value=123)
        result = _extract_output(model)

        assert result == {"final_state": {"name": "test", "value": 123}}

    def test_returns_none_for_unknown_type(self) -> None:
        """Test that unknown types return None."""
        from orcheo_backend.worker.tasks import _extract_output

        result = _extract_output("just a string")
        assert result is None

    def test_returns_none_for_none_input(self) -> None:
        """Test that None input returns None."""
        from orcheo_backend.worker.tasks import _extract_output

        result = _extract_output(None)
        assert result is None

    def test_extracts_object_with_model_dump(self) -> None:
        """Test extraction of non-Pydantic object with model_dump method."""
        from orcheo_backend.worker.tasks import _extract_output

        class CustomObject:
            def model_dump(self) -> dict[str, Any]:
                return {"custom": "data"}

        obj = CustomObject()
        result = _extract_output(obj)

        assert result == {"final_state": {"custom": "data"}}


class TestHandleExecutionFailure:
    """Tests for _handle_execution_failure function."""

    @pytest.mark.asyncio
    async def test_marks_run_as_failed(self, mock_run: MagicMock) -> None:
        """Test that run is marked as failed."""
        from orcheo_backend.worker.tasks import _handle_execution_failure

        mock_repo = AsyncMock()
        exception = ValueError("Workflow crashed")

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            with patch("orcheo_backend.worker.tasks.logger"):
                result = await _handle_execution_failure(mock_run, exception)

        assert result["status"] == "failed"
        assert "Workflow crashed" in result["error"]
        mock_repo.mark_run_failed.assert_called_once_with(
            mock_run.id, actor="worker", error="Workflow crashed"
        )

    @pytest.mark.asyncio
    async def test_logs_exception_when_mark_failed_fails(
        self, mock_run: MagicMock
    ) -> None:
        """Test that exception is logged if mark_run_failed fails."""
        from orcheo_backend.worker.tasks import _handle_execution_failure

        mock_repo = AsyncMock()
        mock_repo.mark_run_failed = AsyncMock(
            side_effect=RuntimeError("Database error")
        )
        exception = ValueError("Original error")

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            with patch("orcheo_backend.worker.tasks.logger") as mock_logger:
                result = await _handle_execution_failure(mock_run, exception)

        # Should still return the original error
        assert result["status"] == "failed"
        assert "Original error" in result["error"]
        # Should log both the original exception and the mark_run_failed failure
        assert mock_logger.exception.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_error_message_from_exception(
        self, mock_run: MagicMock
    ) -> None:
        """Test that error message comes from the exception."""
        from orcheo_backend.worker.tasks import _handle_execution_failure

        mock_repo = AsyncMock()
        exception = RuntimeError("Specific runtime error")

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            with patch("orcheo_backend.worker.tasks.logger"):
                result = await _handle_execution_failure(mock_run, exception)

        assert result["error"] == "Specific runtime error"

    @pytest.mark.asyncio
    async def test_logs_exception_when_history_mark_failed_fails(
        self, mock_run: MagicMock
    ) -> None:
        """Test history failures are logged without changing the return payload."""
        from orcheo_backend.worker.tasks import _handle_execution_failure

        mock_repo = AsyncMock()
        mock_history = AsyncMock()
        mock_history.mark_failed = AsyncMock(side_effect=RuntimeError("history down"))
        exception = RuntimeError("Execution exploded")

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            with patch("orcheo_backend.worker.tasks.logger") as mock_logger:
                result = await _handle_execution_failure(
                    mock_run, exception, history_store=mock_history
                )

        assert result == {"status": "failed", "error": "Execution exploded"}
        mock_history.append_step.assert_awaited_once_with(
            str(mock_run.id), {"status": "error", "error": "Execution exploded"}
        )
        mock_history.mark_failed.assert_awaited_once_with(
            str(mock_run.id), "Execution exploded"
        )
        assert mock_logger.exception.call_count == 2


class TestExecuteWorkflow:
    """Tests for _execute_workflow function."""

    @pytest.mark.asyncio
    async def test_successful_execution(
        self, mock_run: MagicMock, mock_version: MagicMock
    ) -> None:
        """Test successful workflow execution."""
        from orcheo_backend.worker.tasks import _execute_workflow

        mock_repo = AsyncMock()
        mock_repo.get_version = AsyncMock(return_value=mock_version)
        mock_repo.mark_run_succeeded = AsyncMock()
        mock_history = AsyncMock()

        async def _step_stream() -> Any:
            yield {"node_a": {"status": "running"}}

        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.astream = MagicMock(return_value=_step_stream())
        mock_compiled.aget_state = AsyncMock(return_value=MagicMock(values={}))
        mock_graph.compile = MagicMock(return_value=mock_compiled)

        mock_checkpointer = MagicMock()
        mock_checkpointer.__aenter__ = AsyncMock(return_value=mock_checkpointer)
        mock_checkpointer.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            with patch(
                "orcheo_backend.app.dependencies.get_vault",
                return_value=MagicMock(),
            ):
                with patch(
                    "orcheo_backend.app.dependencies.get_history_store",
                    return_value=mock_history,
                ):
                    with patch(
                        "orcheo.graph.builder.build_graph", return_value=mock_graph
                    ):
                        with patch(
                            "orcheo.persistence.create_checkpointer",
                            return_value=mock_checkpointer,
                        ):
                            with patch(
                                "orcheo.persistence.create_graph_store",
                                return_value=mock_checkpointer,
                            ):
                                with patch(
                                    "orcheo_backend.app.workflow_execution._build_initial_state",
                                    return_value={},
                                ):
                                    with patch("orcheo.config.get_settings"):
                                        with patch(
                                            "orcheo.runtime.runnable_config.merge_runnable_configs"
                                        ) as mock_merge:
                                            mock_config = MagicMock()
                                            mock_config.to_runnable_config = MagicMock(
                                                return_value={}
                                            )
                                            mock_config.to_state_config = MagicMock(
                                                return_value={}
                                            )
                                            mock_config.to_json_config = MagicMock(
                                                return_value={}
                                            )
                                            mock_config.tags = []
                                            mock_config.callbacks = []
                                            mock_config.metadata = {}
                                            mock_config.run_name = None
                                            mock_merge.return_value = mock_config

                                            with patch(
                                                "orcheo.runtime.credentials.credential_resolution"
                                            ):
                                                result = await _execute_workflow(
                                                    mock_run
                                                )

        assert result["status"] == "succeeded"
        mock_repo.mark_run_succeeded.assert_called_once()
        mock_graph.compile.assert_called_once_with(
            checkpointer=mock_checkpointer,
            store=mock_checkpointer,
        )
        mock_history.start_run.assert_awaited_once()
        mock_history.append_step.assert_awaited()
        mock_history.mark_completed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_execution_exception(
        self, mock_run: MagicMock, mock_version: MagicMock
    ) -> None:
        """Test that execution exceptions are handled."""
        from orcheo_backend.worker.tasks import _execute_workflow

        mock_repo = AsyncMock()
        mock_repo.get_version = AsyncMock(side_effect=RuntimeError("Version not found"))
        mock_repo.mark_run_failed = AsyncMock()
        mock_history = AsyncMock()

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            with patch(
                "orcheo_backend.app.dependencies.get_vault",
                return_value=MagicMock(),
            ):
                with patch(
                    "orcheo_backend.app.dependencies.get_history_store",
                    return_value=mock_history,
                ):
                    with patch("orcheo.config.get_settings"):
                        with patch("orcheo_backend.worker.tasks.logger"):
                            result = await _execute_workflow(mock_run)

        assert result["status"] == "failed"
        assert "Version not found" in result["error"]
        mock_history.append_step.assert_awaited_once()
        mock_history.mark_failed.assert_awaited_once()


class TestHistoryHelpers:
    """Tests for helper functions that persist run history."""

    @pytest.mark.asyncio
    async def test_start_history_record_logs_history_error(self) -> None:
        """Test _start_history_record catches history store errors."""
        from orcheo_backend.worker.tasks import _start_history_record

        history_error_cls = type("HistoryError", (Exception,), {})
        mock_history = AsyncMock()
        mock_history.start_run = AsyncMock(side_effect=history_error_cls("oops"))
        mock_config = MagicMock()
        mock_config.to_json_config.return_value = {"x": 1}
        mock_config.tags = []
        mock_config.callbacks = []
        mock_config.metadata = {}
        mock_config.run_name = None

        with patch("orcheo_backend.worker.tasks.logger") as mock_logger:
            await _start_history_record(
                history_store=mock_history,
                workflow_id=str(uuid4()),
                execution_id=str(uuid4()),
                inputs={"k": "v"},
                merged_config=mock_config,
                history_error_cls=history_error_cls,
            )

        mock_logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_run_history_steps_logs_history_error(self) -> None:
        """Test _stream_run_history_steps catches append failures."""
        from orcheo_backend.worker.tasks import _stream_run_history_steps

        history_error_cls = type("HistoryError", (Exception,), {})
        mock_history = AsyncMock()
        mock_history.append_step = AsyncMock(side_effect=history_error_cls("append"))
        mock_compiled = MagicMock()

        async def _step_stream() -> Any:
            yield {"node": {"status": "running"}}

        mock_compiled.astream = MagicMock(return_value=_step_stream())

        with patch("orcheo_backend.worker.tasks.logger") as mock_logger:
            await _stream_run_history_steps(
                compiled=mock_compiled,
                state={},
                runtime_config={},
                history_store=mock_history,
                execution_id=str(uuid4()),
                history_error_cls=history_error_cls,
            )

        mock_logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_history_completed_logs_history_error(self) -> None:
        """Test _mark_history_completed catches completion failures."""
        from orcheo_backend.worker.tasks import _mark_history_completed

        history_error_cls = type("HistoryError", (Exception,), {})
        mock_history = AsyncMock()
        mock_history.mark_completed = AsyncMock(side_effect=history_error_cls("done"))

        with patch("orcheo_backend.worker.tasks.logger") as mock_logger:
            await _mark_history_completed(
                history_store=mock_history,
                execution_id=str(uuid4()),
                history_error_cls=history_error_cls,
            )

        mock_history.append_step.assert_awaited_once()
        mock_logger.exception.assert_called_once()


class TestExecuteRunAsync:
    """Tests for _execute_run_async function."""

    @pytest.mark.asyncio
    async def test_returns_error_when_load_fails(self) -> None:
        """Test that load failure returns error."""
        from orcheo_backend.worker.tasks import _execute_run_async

        with patch(
            "orcheo_backend.worker.tasks._load_and_validate_run",
            return_value=(None, {"status": "failed", "error": "Not found"}),
        ):
            result = await _execute_run_async(str(uuid4()), "tenant-1")

        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_returns_error_when_mark_started_fails(
        self, mock_run: MagicMock
    ) -> None:
        """Test that mark_started failure returns error."""
        from orcheo_backend.worker.tasks import _execute_run_async

        with patch(
            "orcheo_backend.worker.tasks._load_and_validate_run",
            return_value=(mock_run, None),
        ):
            with patch(
                "orcheo_backend.worker.tasks._mark_run_started",
                return_value={"status": "skipped", "reason": "Already started"},
            ):
                result = await _execute_run_async(str(mock_run.id), "tenant-1")

        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_calls_execute_workflow_on_success(self, mock_run: MagicMock) -> None:
        """Test that _execute_workflow is called when validation succeeds."""
        from orcheo_backend.worker.tasks import _execute_run_async

        with patch(
            "orcheo_backend.worker.tasks._load_and_validate_run",
            return_value=(mock_run, None),
        ):
            with patch(
                "orcheo_backend.worker.tasks._mark_run_started",
                return_value=None,
            ):
                with patch(
                    "orcheo_backend.worker.tasks._execute_workflow",
                    return_value={"status": "succeeded"},
                ) as mock_execute:
                    result = await _execute_run_async(str(mock_run.id), "tenant-1")

        assert result["status"] == "succeeded"
        mock_execute.assert_called_once_with(mock_run)


class TestDispatchCronTriggersAsync:
    """Tests for _dispatch_cron_triggers_async function."""

    @pytest.mark.asyncio
    async def test_dispatches_due_runs(self) -> None:
        """Test that due cron runs are dispatched."""
        from orcheo_backend.worker.tasks import _dispatch_cron_triggers_async

        mock_run1 = MagicMock()
        mock_run1.id = uuid4()
        mock_run2 = MagicMock()
        mock_run2.id = uuid4()

        mock_repo = AsyncMock()
        mock_repo.dispatch_due_cron_runs = AsyncMock(
            return_value=[mock_run1, mock_run2]
        )

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            result = await _dispatch_cron_triggers_async()

        assert len(result) == 2
        assert str(mock_run1.id) in result
        assert str(mock_run2.id) in result

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_runs_due(self) -> None:
        """Test that empty list is returned when no runs are due."""
        from orcheo_backend.worker.tasks import _dispatch_cron_triggers_async

        mock_repo = AsyncMock()
        mock_repo.dispatch_due_cron_runs = AsyncMock(return_value=[])

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            result = await _dispatch_cron_triggers_async()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_run_ids(self) -> None:
        """Test that dispatched runs are returned."""
        from orcheo_backend.worker.tasks import _dispatch_cron_triggers_async

        mock_run = MagicMock()
        mock_run.id = uuid4()

        mock_repo = AsyncMock()
        mock_repo.dispatch_due_cron_runs = AsyncMock(return_value=[mock_run])

        with patch(
            "orcheo_backend.app.dependencies.get_repository", return_value=mock_repo
        ):
            result = await _dispatch_cron_triggers_async()

        assert result == [str(mock_run.id)]
