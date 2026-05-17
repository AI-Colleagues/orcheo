"""Factories for repository, vault, and credential service instances."""

from __future__ import annotations
from typing import Any, cast
from dynaconf import Dynaconf
from orcheo.models import AesGcmCredentialCipher
from orcheo.vault import BaseCredentialVault
from orcheo.vault.oauth import OAuthCredentialService
from orcheo_backend.app.agentensor.checkpoint_store import (
    PostgresAgentensorCheckpointStore,
)
from orcheo_backend.app.history import (
    PostgresRunHistoryStore,
    RunHistoryStore,
)
from orcheo_backend.app.plugin_installation_store import (
    PostgresPluginInstallationStore,
)
from orcheo_backend.app.repository import WorkflowRepository
from orcheo_backend.app.repository_postgres import PostgresWorkflowRepository


def settings_value(
    settings: Dynaconf,
    *,
    attr_path: str | None,
    env_key: str,
    default: Any,
) -> Any:
    """Return a configuration value supporting Dynaconf attribute access."""
    if hasattr(settings, "get"):
        try:
            value = settings.get(env_key, default)  # type: ignore[call-arg]
        except TypeError:  # pragma: no cover - defensive fallback
            value = default
        return cast(Any, value)

    if attr_path:
        current: object = settings
        for part in attr_path.split("."):
            if not hasattr(current, part):
                break
            current = getattr(current, part)
        else:
            return cast(Any, current)

    return default


def create_vault(settings: Dynaconf) -> BaseCredentialVault:
    """Create a credential vault based on configured backend."""
    backend = cast(
        str,
        settings_value(
            settings,
            attr_path="vault.backend",
            env_key="VAULT_BACKEND",
            default="postgres",
        ),
    )
    key = cast(
        str | None,
        settings_value(
            settings,
            attr_path="vault.encryption_key",
            env_key="VAULT_ENCRYPTION_KEY",
            default=None,
        ),
    )
    if backend != "postgres":
        msg = "Vault backend must be 'postgres'."
        raise ValueError(msg)

    from orcheo.vault.postgres import PostgresCredentialVault

    dsn = cast(
        str,
        settings_value(
            settings,
            attr_path="postgres_dsn",
            env_key="POSTGRES_DSN",
            default=None,
        ),
    )
    if not dsn:
        msg = "ORCHEO_POSTGRES_DSN must be set when using the postgres backend."
        raise ValueError(msg)
    pool_min_size = int(
        settings_value(
            settings,
            attr_path="postgres_pool_min_size",
            env_key="POSTGRES_POOL_MIN_SIZE",
            default=1,
        )
    )
    pool_max_size = int(
        settings_value(
            settings,
            attr_path="postgres_pool_max_size",
            env_key="POSTGRES_POOL_MAX_SIZE",
            default=10,
        )
    )
    if not key:
        msg = "ORCHEO_VAULT_ENCRYPTION_KEY must be set when using postgres."
        raise ValueError(msg)
    cipher = AesGcmCredentialCipher(key=key)
    return PostgresCredentialVault(
        dsn,
        cipher=cipher,
        pool_min_size=pool_min_size,
        pool_max_size=pool_max_size,
    )


def ensure_credential_service(
    settings: Dynaconf,
    vault: BaseCredentialVault,
) -> OAuthCredentialService:
    """Initialise the OAuth credential service with configured TTL."""
    token_ttl = cast(
        int,
        settings_value(
            settings,
            attr_path="vault.token_ttl_seconds",
            env_key="VAULT_TOKEN_TTL_SECONDS",
            default=3600,
        ),
    )
    return OAuthCredentialService(vault, token_ttl_seconds=token_ttl)


def create_repository(  # noqa: C901
    settings: Dynaconf,
    credential_service: OAuthCredentialService,
    history_store_ref: dict[str, RunHistoryStore],
    checkpoint_store_ref: dict[str, object] | None = None,
    plugin_installation_store_ref: dict[str, object] | None = None,
) -> WorkflowRepository:
    """Create the workflow repository using configured backend."""
    backend = cast(
        str,
        settings_value(
            settings,
            attr_path="repository_backend",
            env_key="REPOSITORY_BACKEND",
            default="postgres",
        ),
    )

    if backend != "postgres":
        msg = "Repository backend must be 'postgres'."
        raise ValueError(msg)

    dsn = cast(
        str,
        settings_value(
            settings,
            attr_path="postgres_dsn",
            env_key="POSTGRES_DSN",
            default=None,
        ),
    )
    if not dsn:  # pragma: no cover - defensive, validated earlier
        msg = "ORCHEO_POSTGRES_DSN must be set when using the postgres backend."
        raise ValueError(msg)
    pool_min_size = cast(
        int,
        settings_value(
            settings,
            attr_path="postgres_pool_min_size",
            env_key="POSTGRES_POOL_MIN_SIZE",
            default=1,
        ),
    )
    pool_max_size = cast(
        int,
        settings_value(
            settings,
            attr_path="postgres_pool_max_size",
            env_key="POSTGRES_POOL_MAX_SIZE",
            default=10,
        ),
    )
    pool_timeout = cast(
        float,
        settings_value(
            settings,
            attr_path="postgres_pool_timeout",
            env_key="POSTGRES_POOL_TIMEOUT",
            default=30.0,
        ),
    )
    pool_max_idle = cast(
        float,
        settings_value(
            settings,
            attr_path="postgres_pool_max_idle",
            env_key="POSTGRES_POOL_MAX_IDLE",
            default=300.0,
        ),
    )
    history_store_ref["store"] = PostgresRunHistoryStore(
        dsn,
        pool_min_size=pool_min_size,
        pool_max_size=pool_max_size,
        pool_timeout=pool_timeout,
        pool_max_idle=pool_max_idle,
    )
    if checkpoint_store_ref is not None:  # pragma: no branch
        checkpoint_store_ref["store"] = PostgresAgentensorCheckpointStore(
            dsn,
            pool_min_size=pool_min_size,
            pool_max_size=pool_max_size,
            pool_timeout=pool_timeout,
            pool_max_idle=pool_max_idle,
        )
    if plugin_installation_store_ref is not None:
        plugin_installation_store_ref["store"] = PostgresPluginInstallationStore(
            dsn,
            pool_min_size=pool_min_size,
            pool_max_size=pool_max_size,
            pool_timeout=pool_timeout,
            pool_max_idle=pool_max_idle,
        )
    return PostgresWorkflowRepository(
        dsn,
        credential_service=credential_service,
        pool_min_size=pool_min_size,
        pool_max_size=pool_max_size,
        pool_timeout=pool_timeout,
        pool_max_idle=pool_max_idle,
    )


__all__ = [
    "create_repository",
    "create_vault",
    "ensure_credential_service",
    "settings_value",
]
