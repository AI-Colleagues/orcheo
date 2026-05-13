"""Tests for exception helper functions in ``orcheo_backend.app``."""

from __future__ import annotations
import pytest
from fastapi import HTTPException
from orcheo.triggers.webhook import WebhookValidationError
from orcheo.vault import WorkflowScopeError
from orcheo_backend.app import (
    _raise_conflict,
    _raise_not_found,
    _raise_scope_error,
    _raise_webhook_error,
)
from orcheo_backend.app.errors import (
    WorkspaceQuotaExceededError,
    WorkspaceRateLimitError,
)


def test_raise_not_found_raises_404() -> None:
    """The _raise_not_found helper raises a 404 HTTPException."""
    with pytest.raises(HTTPException) as exc_info:
        _raise_not_found("Test not found", ValueError("test"))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Test not found"


def test_raise_conflict_raises_409() -> None:
    """The _raise_conflict helper raises a 409 HTTPException."""
    with pytest.raises(HTTPException) as exc_info:
        _raise_conflict("Test conflict", ValueError("test"))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Test conflict"


def test_raise_webhook_error_raises_with_status_code() -> None:
    """_raise_webhook_error raises HTTPException with webhook error status."""
    webhook_error = WebhookValidationError("Invalid signature", status_code=401)
    with pytest.raises(HTTPException) as exc_info:
        _raise_webhook_error(webhook_error)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid signature"


def test_raise_scope_error_raises_403() -> None:
    """The _raise_scope_error helper raises a 403 HTTPException."""
    scope_error = WorkflowScopeError("Access denied")
    with pytest.raises(HTTPException) as exc_info:
        _raise_scope_error(scope_error)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access denied"


def test_workspace_limit_error_as_http_exception_includes_retry_after() -> None:
    """WorkspaceLimitError serializes structured details and Retry-After."""

    error = WorkspaceRateLimitError(
        "Rate limit exceeded",
        code="workspace.rate_limited",
        status_code=429,
        details={"limit": 10, "window": "1m"},
        retry_after=60,
    )

    http_exc = error.as_http_exception()

    assert http_exc.status_code == 429
    assert http_exc.detail == {
        "error": {
            "code": "workspace.rate_limited",
            "message": "Rate limit exceeded",
            "details": {"limit": 10, "window": "1m"},
        }
    }
    assert http_exc.headers == {"Retry-After": "60"}


def test_workspace_limit_error_as_http_exception_without_retry_after() -> None:
    """WorkspaceLimitError omits headers when no retry hint is provided."""

    error = WorkspaceQuotaExceededError(
        "Quota exceeded",
        code="workspace.quota_exceeded",
        details={"quota": "workflows"},
    )

    http_exc = error.as_http_exception()

    assert http_exc.status_code == 429
    assert http_exc.detail["error"]["code"] == "workspace.quota_exceeded"
    assert http_exc.headers is None
