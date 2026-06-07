"""PostgreSQL-backed workflow repository split across focused mixins."""

from __future__ import annotations
from orcheo.vault.oauth import OAuthCredentialService
from orcheo_backend.app.repository_postgres._listeners import ListenerRepositoryMixin
from orcheo_backend.app.repository_postgres._retry import RetryPolicyMixin
from orcheo_backend.app.repository_postgres._runs import WorkflowRunMixin
from orcheo_backend.app.repository_postgres._triggers import TriggerRepositoryMixin
from orcheo_backend.app.repository_postgres._versions import WorkflowVersionMixin
from orcheo_backend.app.repository_postgres._workflows import WorkflowRepositoryMixin


class PostgresWorkflowRepository(
    ListenerRepositoryMixin,
    TriggerRepositoryMixin,
    RetryPolicyMixin,
    WorkflowRunMixin,
    WorkflowVersionMixin,
    WorkflowRepositoryMixin,
):
    """PostgreSQL-backed workflow repository for production deployments."""

    def __init__(
        self,
        dsn: str,
        *,
        credential_service: OAuthCredentialService | None = None,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        pool_timeout: float = 30.0,
        pool_max_idle: float = 300.0,
    ) -> None:
        """Configure repository dependencies and initialize trigger layer."""
        super().__init__(
            dsn,
            credential_service=credential_service,
            pool_min_size=pool_min_size,
            pool_max_size=pool_max_size,
            pool_timeout=pool_timeout,
            pool_max_idle=pool_max_idle,
        )


__all__ = ["PostgresWorkflowRepository"]
