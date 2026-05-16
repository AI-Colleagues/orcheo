"""Tests for repository package exports."""

from __future__ import annotations
import importlib
import pytest


def test_repository_module_exports_workflow_repository() -> None:
    """The repository package exposes the workflow repository protocol."""
    repository = importlib.import_module("orcheo_backend.app.repository")
    assert repository.WorkflowRepository is not None
    assert repository.InMemoryWorkflowRepository is not None


def test_repository_module_rejects_unknown_attribute() -> None:
    """Unknown attributes should raise AttributeError via __getattr__."""
    repository = importlib.import_module("orcheo_backend.app.repository")

    with pytest.raises(AttributeError):
        repository.UnknownRepository  # noqa: B018
