"""SQLite-backed workflow repository split across focused mixins."""

from __future__ import annotations
from pathlib import Path
from orcheo.vault.oauth import OAuthCredentialService
from orcheo_backend.app.repository_sqlite._listeners import ListenerRepositoryMixin
from orcheo_backend.app.repository_sqlite._remediations import WorkflowRemediationMixin
from orcheo_backend.app.repository_sqlite._retry import RetryPolicyMixin
from orcheo_backend.app.repository_sqlite._runs import WorkflowRunMixin
from orcheo_backend.app.repository_sqlite._triggers import TriggerRepositoryMixin
from orcheo_backend.app.repository_sqlite._versions import WorkflowVersionMixin
from orcheo_backend.app.repository_sqlite._workflows import WorkflowRepositoryMixin


class SqliteWorkflowRepository(
    ListenerRepositoryMixin,
    WorkflowRemediationMixin,
    TriggerRepositoryMixin,
    RetryPolicyMixin,
    WorkflowRunMixin,
    WorkflowVersionMixin,
    WorkflowRepositoryMixin,
):
    """SQLite-backed workflow repository for durable local development state."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        credential_service: OAuthCredentialService | None = None,
    ) -> None:
        """Configure repository dependencies and initialize trigger layer."""
        super().__init__(database_path, credential_service=credential_service)


__all__ = ["SqliteWorkflowRepository"]
