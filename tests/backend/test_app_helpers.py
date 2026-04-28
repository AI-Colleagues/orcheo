"""Tests for various helper functions in ``orcheo_backend.app``."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
from orcheo.models import CredentialHealthStatus
from orcheo.vault.oauth import CredentialHealthReport, CredentialHealthResult
from orcheo_backend.app.history import RunHistoryRecord


def test_scope_from_access_scoped() -> None:
    """_scope_from_access returns workflow scope for scoped access."""
    from orcheo_backend.app import _scope_from_access

    workflow_id = uuid4()
    scope = _scope_from_access("scoped", workflow_id)

    assert scope is not None
    assert workflow_id in scope.workflow_ids


def test_scope_from_access_scoped_without_workflow() -> None:
    """_scope_from_access raises error when scoped lacks a workflow."""
    import pytest
    from orcheo_backend.app import _scope_from_access

    with pytest.raises(ValueError) as exc_info:
        _scope_from_access("scoped", None)
    
    assert "workflow_id is required when access is set to scoped" in str(exc_info.value)


def test_scope_from_access_shared() -> None:
    """_scope_from_access returns unrestricted scope for shared access."""
    from orcheo_backend.app import _scope_from_access

    scope = _scope_from_access("shared", None)

    assert scope is not None
    assert scope.is_unrestricted()


def test_context_from_workflow_with_id() -> None:
    """_context_from_workflow creates context with workflow ID."""
    from orcheo_backend.app import _context_from_workflow

    workflow_id = uuid4()
    context = _context_from_workflow(workflow_id)

    assert context is not None
    assert context.workflow_id == workflow_id


def test_context_from_workflow_without_id() -> None:
    """_context_from_workflow returns None without workflow ID."""
    from orcheo_backend.app import _context_from_workflow

    context = _context_from_workflow(None)

    assert context is None


def test_history_to_response_with_steps() -> None:
    """_history_to_response converts record with steps to response."""
    from orcheo_backend.app import _history_to_response
    from orcheo_backend.app.history import RunHistoryStep

    record = RunHistoryRecord(
        workflow_id=str(uuid4()),
        execution_id="test-exec",
        inputs={"key": "value"},
    )
    record.steps = [
        RunHistoryStep(index=0, payload={"step": 1}),
        RunHistoryStep(index=1, payload={"step": 2}),
    ]

    response = _history_to_response(record)

    assert response.execution_id == "test-exec"
    assert len(response.steps) == 2


def test_history_to_response_with_from_step() -> None:
    """_history_to_response slices steps from given index."""
    from orcheo_backend.app import _history_to_response
    from orcheo_backend.app.history import RunHistoryStep

    record = RunHistoryRecord(
        workflow_id=str(uuid4()),
        execution_id="test-exec",
        inputs={},
    )
    record.steps = [
        RunHistoryStep(index=0, payload={"step": 1}),
        RunHistoryStep(index=1, payload={"step": 2}),
        RunHistoryStep(index=2, payload={"step": 3}),
    ]

    response = _history_to_response(record, from_step=1)

    assert len(response.steps) == 2
    assert response.steps[0].index == 1


def test_health_report_to_response() -> None:
    """_health_report_to_response converts report to response."""
    from orcheo_backend.app import _health_report_to_response

    workflow_id = uuid4()
    cred_id = uuid4()

    report = CredentialHealthReport(
        workflow_id=workflow_id,
        results=[
            CredentialHealthResult(
                credential_id=cred_id,
                name="Test Cred",
                provider="slack",
                status=CredentialHealthStatus.HEALTHY,
                last_checked_at=datetime.now(tz=UTC),
                failure_reason=None,
            )
        ],
        checked_at=datetime.now(tz=UTC),
    )

    response = _health_report_to_response(report)

    assert response.workflow_id == str(workflow_id)
    assert response.status == CredentialHealthStatus.HEALTHY
    assert len(response.credentials) == 1


def test_health_report_to_response_unhealthy() -> None:
    """_health_report_to_response marks unhealthy reports."""
    from orcheo_backend.app import _health_report_to_response

    workflow_id = uuid4()
    cred_id = uuid4()

    report = CredentialHealthReport(
        workflow_id=workflow_id,
        results=[
            CredentialHealthResult(
                credential_id=cred_id,
                name="Test Cred",
                provider="slack",
                status=CredentialHealthStatus.UNHEALTHY,
                last_checked_at=datetime.now(tz=UTC),
                failure_reason="Token expired",
            )
        ],
        checked_at=datetime.now(tz=UTC),
    )

    response = _health_report_to_response(report)

    assert response.status == CredentialHealthStatus.UNHEALTHY
    assert response.credentials[0].failure_reason == "Token expired"
