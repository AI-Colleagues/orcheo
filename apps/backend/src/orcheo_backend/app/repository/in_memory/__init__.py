"""Concrete in-memory repository implementation."""

from __future__ import annotations
from orcheo.vault.oauth import OAuthCredentialService
from orcheo_backend.app.repository.in_memory.listeners import ListenerRepositoryMixin
from orcheo_backend.app.repository.in_memory.retry import RetryPolicyMixin
from orcheo_backend.app.repository.in_memory.runs import WorkflowRunMixin
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState
from orcheo_backend.app.repository.in_memory.triggers import TriggerDispatchMixin
from orcheo_backend.app.repository.in_memory.versions import WorkflowVersionMixin
from orcheo_backend.app.repository.in_memory.workflows import WorkflowCrudMixin


class InMemoryWorkflowRepository(
    ListenerRepositoryMixin,
    RetryPolicyMixin,
    TriggerDispatchMixin,
    WorkflowRunMixin,
    WorkflowVersionMixin,
    WorkflowCrudMixin,
    InMemoryRepositoryState,
):
    """Simple async-safe in-memory repository for workflows and runs."""

    def __init__(
        self, credential_service: OAuthCredentialService | None = None
    ) -> None:
        """Initialize the repository with an optional credential service."""
        super().__init__(credential_service=credential_service)


__all__ = ["InMemoryWorkflowRepository"]
