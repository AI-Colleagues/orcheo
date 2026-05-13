from __future__ import annotations
import pytest
from fastapi import status
from orcheo_backend.app.workspace import errors as workspace_errors


def test_workspace_http_error_payload_and_codes() -> None:
    """Workspace HTTP errors should carry structured payloads."""

    exc = workspace_errors.WorkspaceHTTPError(
        status_code=status.HTTP_409_CONFLICT,
        message="boom",
        error_code="workspace.conflict",
        details={"field": "slug"},
    )

    assert exc.status_code == status.HTTP_409_CONFLICT
    assert exc.error_code == "workspace.conflict"
    assert exc.message == "boom"
    assert exc.detail["error"]["details"] == {"field": "slug"}


def test_workspace_context_required_error_defaults() -> None:
    """The workspace-required error should default to the expected code."""

    exc = workspace_errors.WorkspaceContextRequiredError()

    assert exc.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.error_code == "workspace.required"


def test_workspace_error_helpers_raise_expected_types() -> None:
    """Helper functions should raise the matching HTTP exceptions."""

    with pytest.raises(workspace_errors.WorkspaceHTTPError) as exc_info:
        workspace_errors.raise_workspace_not_found()
    assert exc_info.value.error_code == "workspace.not_found"

    with pytest.raises(workspace_errors.WorkspaceHTTPError) as exc_info:
        workspace_errors.raise_workspace_forbidden(
            "nope", error_code="workspace.denied"
        )
    assert exc_info.value.error_code == "workspace.denied"

    with pytest.raises(workspace_errors.WorkspaceContextRequiredError):
        workspace_errors.raise_workspace_required()
