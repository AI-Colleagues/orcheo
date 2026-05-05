"""Tests for trigger enqueue failure and success handling in triggers router."""

from __future__ import annotations
import builtins
import importlib
import sys
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient


backend_app = importlib.import_module("orcheo_backend.app")


def _langgraph_script() -> str:
    return """
from langgraph.graph import END, START, StateGraph

def build_graph():
    graph = StateGraph(dict)
    graph.add_node("start", lambda state: state)
    graph.add_edge(START, "start")
    graph.add_edge("start", END)
    return graph
""".strip()


class TestEnqueueRunFunction:
    """Direct tests for the _enqueue_run_for_execution function."""

    def test_enqueue_run_success_logs_info(self) -> None:
        """Test that successful enqueue logs an info message."""
        from orcheo.models.workflow import WorkflowRun, WorkflowRunStatus

        run = WorkflowRun(
            id=uuid4(),
            tenant_id="tenant-1",
            workflow_version_id=uuid4(),
            status=WorkflowRunStatus.PENDING,
            triggered_by="test",
        )

        mock_execute_run = MagicMock()
        mock_execute_run.apply_async = MagicMock()

        # Mock the worker.tasks module at the point of import
        mock_tasks_module = MagicMock()
        mock_tasks_module.execute_run = mock_execute_run

        with patch(
            "orcheo_backend.app.repository_sqlite._triggers.logger"
        ) as mock_logger:
            # Patch the import mechanism to return our mock
            original_import = builtins.__import__

            def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
                if name == "orcheo_backend.worker.tasks":
                    return mock_tasks_module
                return original_import(name, *args, **kwargs)

            # Remove cached module to force re-import
            saved_module = sys.modules.pop("orcheo_backend.worker.tasks", None)

            try:
                with patch.object(builtins, "__import__", side_effect=mock_import):
                    # Import and call the function
                    from orcheo_backend.app.repository_sqlite._triggers import (
                        _enqueue_run_for_execution,
                    )

                    _enqueue_run_for_execution(run)

                # Check that delay was called (indicating successful execution)
                mock_execute_run.apply_async.assert_called_once_with(
                    args=(str(run.id),),
                    headers={"tenant_id": "tenant-1"},
                )
                # Check that info log was called
                mock_logger.info.assert_called_once()
                call_args = mock_logger.info.call_args[0]
                assert "Enqueued run" in call_args[0]
            finally:
                # Restore the module
                if saved_module is not None:
                    sys.modules["orcheo_backend.worker.tasks"] = saved_module

    def test_enqueue_run_exception_logs_warning(self) -> None:
        """Test that exception during enqueue logs a warning."""
        from orcheo.models.workflow import WorkflowRun, WorkflowRunStatus

        run = WorkflowRun(
            id=uuid4(),
            tenant_id="tenant-1",
            workflow_version_id=uuid4(),
            status=WorkflowRunStatus.PENDING,
            triggered_by="test",
        )

        mock_execute_run = MagicMock()
        mock_execute_run.apply_async = MagicMock(
            side_effect=ConnectionError("Redis unavailable")
        )

        mock_tasks_module = MagicMock()
        mock_tasks_module.execute_run = mock_execute_run

        with patch(
            "orcheo_backend.app.repository_sqlite._triggers.logger"
        ) as mock_logger:
            original_import = builtins.__import__

            def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
                if name == "orcheo_backend.worker.tasks":
                    return mock_tasks_module
                return original_import(name, *args, **kwargs)

            saved_module = sys.modules.pop("orcheo_backend.worker.tasks", None)

            try:
                with patch.object(builtins, "__import__", side_effect=mock_import):
                    from orcheo_backend.app.repository_sqlite._triggers import (
                        _enqueue_run_for_execution,
                    )

                    _enqueue_run_for_execution(run)

                # Verify warning was logged
                mock_logger.warning.assert_called_once()
            finally:
                if saved_module is not None:
                    sys.modules["orcheo_backend.worker.tasks"] = saved_module


def test_enqueue_run_logs_warning_on_celery_failure(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that _enqueue_run_for_execution logs warning if Celery unavailable."""
    workflow_response = api_client.post(
        "/api/workflows",
        json={"name": "Enqueue Test Flow", "actor": "tester"},
    )
    workflow_id = workflow_response.json()["id"]

    api_client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={
            "script": _langgraph_script(),
            "entrypoint": "build_graph",
            "metadata": {},
            "created_by": "tester",
        },
    )

    # Mock the execute_run task at the worker.tasks level
    mock_execute_run = MagicMock()
    mock_execute_run.apply_async = MagicMock(
        side_effect=ConnectionError("Redis unavailable")
    )

    with patch.dict(
        "sys.modules",
        {"orcheo_backend.worker.tasks": MagicMock(execute_run=mock_execute_run)},
    ):
        # Make the webhook request - should succeed but log warning
        response = api_client.post(f"/api/workflows/{workflow_id}/triggers/webhook")

        # The request should still succeed (202) because enqueue is best-effort
        assert response.status_code == 202


def test_cron_dispatch_enqueue_failure_does_not_block_response(
    api_client: TestClient,
) -> None:
    """Test that cron dispatch returns runs even if enqueueing fails."""
    workflow_response = api_client.post(
        "/api/workflows",
        json={"name": "Cron Enqueue Test", "actor": "tester"},
    )
    workflow_id = workflow_response.json()["id"]

    api_client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={
            "script": _langgraph_script(),
            "entrypoint": "build_graph",
            "metadata": {},
            "created_by": "tester",
        },
    )

    api_client.put(
        f"/api/workflows/{workflow_id}/triggers/cron/config",
        json={
            "expression": "0 * * * *",
            "timezone": "UTC",
        },
    )

    mock_execute_run = MagicMock()
    mock_execute_run.apply_async = MagicMock(side_effect=Exception("Broker down"))

    with patch.dict(
        "sys.modules",
        {"orcheo_backend.worker.tasks": MagicMock(execute_run=mock_execute_run)},
    ):
        response = api_client.post(
            "/api/triggers/cron/dispatch",
            json={"now": datetime(2025, 1, 1, 0, 0, tzinfo=UTC).isoformat()},
        )

        # Dispatch should succeed and return the runs
        assert response.status_code == 200
        runs = response.json()
        assert len(runs) == 1


def test_manual_dispatch_enqueue_failure_does_not_block_response(
    api_client: TestClient,
) -> None:
    """Test that manual dispatch returns runs even if enqueueing fails."""
    workflow_response = api_client.post(
        "/api/workflows",
        json={"name": "Manual Enqueue Test", "actor": "tester"},
    )
    workflow_id = workflow_response.json()["id"]

    api_client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={
            "script": _langgraph_script(),
            "entrypoint": "build_graph",
            "metadata": {},
            "created_by": "tester",
        },
    )

    mock_execute_run = MagicMock()
    mock_execute_run.apply_async = MagicMock(side_effect=RuntimeError("Worker offline"))

    with patch.dict(
        "sys.modules",
        {"orcheo_backend.worker.tasks": MagicMock(execute_run=mock_execute_run)},
    ):
        response = api_client.post(
            "/api/triggers/manual/dispatch",
            json={
                "workflow_id": workflow_id,
                "actor": "operator",
                "runs": [{"input_payload": {"test": "data"}}],
            },
        )

        # Manual dispatch should succeed and return the runs
        assert response.status_code == 200
        runs = response.json()
        assert len(runs) == 1
        assert runs[0]["input_payload"] == {"test": "data"}


def test_enqueue_run_import_failure_is_handled(
    api_client: TestClient,
) -> None:
    """Test that import failure during enqueue is handled gracefully."""
    workflow_response = api_client.post(
        "/api/workflows",
        json={"name": "Import Fail Test", "actor": "tester"},
    )
    workflow_id = workflow_response.json()["id"]

    api_client.post(
        f"/api/workflows/{workflow_id}/versions/ingest",
        json={
            "script": _langgraph_script(),
            "entrypoint": "build_graph",
            "metadata": {},
            "created_by": "tester",
        },
    )

    # Simulate an import error by patching sys.modules
    original_import = builtins.__import__

    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if "orcheo_backend.worker.tasks" in name:
            msg = "No module named 'celery'"
            raise ImportError(msg)
        return original_import(name, *args, **kwargs)

    # Save the original module if it exists
    saved_module = sys.modules.pop("orcheo_backend.worker.tasks", None)

    try:
        with patch.object(builtins, "__import__", side_effect=mock_import):
            response = api_client.post(f"/api/workflows/{workflow_id}/triggers/webhook")

            # Request should still succeed
            assert response.status_code == 202
    finally:
        # Restore the module if it existed
        if saved_module is not None:
            sys.modules["orcheo_backend.worker.tasks"] = saved_module
