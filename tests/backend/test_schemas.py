"""Validation tests for FastAPI request schemas."""

from __future__ import annotations
import pytest
from pydantic import ValidationError
from orcheo.graph.ingestion import DEFAULT_SCRIPT_SIZE_LIMIT
from orcheo_backend.app.schemas.workflows import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    WorkflowVersionIngestRequest,
)


def test_workflow_version_ingest_request_rejects_large_scripts() -> None:
    """Submitting scripts larger than the configured limit raises a validation error."""

    oversized = "a" * (DEFAULT_SCRIPT_SIZE_LIMIT + 1)

    with pytest.raises(ValidationError):
        WorkflowVersionIngestRequest(
            script=oversized,
            entrypoint=None,
            metadata={},
            notes=None,
            created_by="tester",
        )


def test_workflow_create_request_allows_explicit_none_handle() -> None:
    request = WorkflowCreateRequest(name="Schema Flow", handle=None)

    assert request.handle is None


def test_workflow_update_request_allows_explicit_none_handle() -> None:
    request = WorkflowUpdateRequest(handle=None)

    assert request.handle is None


def test_workflow_update_request_ignores_legacy_chatkit_fields() -> None:
    legacy_data = {
        "chatkit_start_screen_prompts": [{"label": "One", "prompt": "Hello"}],
        "chatkit_supported_models": [{"id": "openai:gpt-5"}],
    }

    request = WorkflowUpdateRequest(**legacy_data)

    assert request.chatkit is None


def test_workflow_update_request_rejects_conflicting_prompt_flags() -> None:
    with pytest.raises(ValueError, match="clear_chatkit_start_screen_prompts"):
        WorkflowUpdateRequest(
            chatkit={"start_screen_prompts": [{"label": "One", "prompt": "Test"}]},
            clear_chatkit_start_screen_prompts=True,
        )


def test_workflow_update_request_rejects_conflicting_supported_model_flags() -> None:
    with pytest.raises(ValueError, match="clear_chatkit_supported_models"):
        WorkflowUpdateRequest(
            chatkit={"supported_models": [{"id": "openai:gpt-5"}]},
            clear_chatkit_supported_models=True,
        )


def test_workflow_update_request_validator_passthrough_for_non_dict() -> None:
    """Non-dict input is returned unchanged."""
    from types import SimpleNamespace

    obj = SimpleNamespace()  # all WorkflowUpdateRequest fields are optional/defaulted
    result = WorkflowUpdateRequest.model_validate(obj, from_attributes=True)
    assert result.actor == "system"
