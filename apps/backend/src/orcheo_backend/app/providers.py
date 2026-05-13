"""Factories for repository, vault, and credential service instances."""

from __future__ import annotations
import os
import secrets
from pathlib import Path
from typing import Any, cast
from dynaconf import Dynaconf
from orcheo.models import AesGcmCredentialCipher
from orcheo.vault import (
    BaseCredentialVault,
    FileCredentialVault,
    InMemoryCredentialVault,
)
from orcheo.vault.oauth import OAuthCredentialService
from orcheo_backend.app.agentensor.checkpoint_store import (
    InMemoryAgentensorCheckpointStore,
    PostgresAgentensorCheckpointStore,
    SqliteAgentensorCheckpointStore,
)
from orcheo_backend.app.history import (
    InMemoryRunHistoryStore,
    PostgresRunHistoryStore,
    RunHistoryStore,
    SqliteRunHistoryStore,
)
from orcheo_backend.app.plugin_installation_store import (
    InMemoryPluginInstallationStore,
    PostgresPluginInstallationStore,
    SqlitePluginInstallationStore,
)
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowRepository,
)
from orcheo_backend.app.repository_postgres import PostgresWorkflowRepository
from orcheo_backend.app.repository_sqlite import SqliteWorkflowRepository


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


def ensure_file_vault_key(path: Path, provided_key: str | None) -> str:
    """Load or generate the encryption key for the file-backed credential vault."""
    if provided_key:
        return provided_key

    key_path = path.with_name(f"{path.stem}.key")
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        key = key_path.read_text(encoding="utf-8").strip()
        if key:
            return key

    key = secrets.token_hex(32)
    key_path.write_text(key, encoding="utf-8")
    try:
        os.chmod(key_path, 0o600)
    except (PermissionError, NotImplementedError, OSError):
        pass
    return key


def create_vault(settings: Dynaconf) -> BaseCredentialVault:
    """Create a credential vault based on configured backend."""
    backend = cast(
        str,
        settings_value(
            settings,
            attr_path="vault.backend",
            env_key="VAULT_BACKEND",
            default="file",
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
    if backend == "inmemory":
        encryption_key = key or secrets.token_hex(32)
        cipher = AesGcmCredentialCipher(key=encryption_key)
        return InMemoryCredentialVault(cipher=cipher)
    if backend == "file":
        local_path = cast(
            str,
            settings_value(
                settings,
                attr_path="vault.local_path",
                env_key="VAULT_LOCAL_PATH",
                default=".orcheo/vault.sqlite",
            ),
        )
        path = Path(local_path).expanduser()
        encryption_key = ensure_file_vault_key(path, key)
        cipher = AesGcmCredentialCipher(key=encryption_key)
        return FileCredentialVault(path, cipher=cipher)
    if backend == "postgres":
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
    msg = "Vault backend 'aws_kms' is not supported in this environment."
    raise ValueError(msg)


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
            default="sqlite",
        ),
    )

    if backend == "sqlite":
        sqlite_path = cast(
            str,
            settings_value(
                settings,
                attr_path="repository_sqlite_path",
                env_key="REPOSITORY_SQLITE_PATH",
                default="~/.orcheo/workflows.sqlite",
            ),
        )
        history_store_ref["store"] = SqliteRunHistoryStore(sqlite_path)
        if checkpoint_store_ref is not None:  # pragma: no branch
            checkpoint_store_ref["store"] = SqliteAgentensorCheckpointStore(sqlite_path)
        if plugin_installation_store_ref is not None:
            plugin_installation_store_ref["store"] = SqlitePluginInstallationStore(
                sqlite_path
            )
        return SqliteWorkflowRepository(
            sqlite_path,
            credential_service=credential_service,
        )
    if backend == "inmemory":
        history_store_ref["store"] = InMemoryRunHistoryStore()
        if checkpoint_store_ref is not None:  # pragma: no branch
            checkpoint_store_ref["store"] = InMemoryAgentensorCheckpointStore()
        if plugin_installation_store_ref is not None:
            plugin_installation_store_ref["store"] = InMemoryPluginInstallationStore()
        return InMemoryWorkflowRepository(credential_service=credential_service)
    if backend == "postgres":
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
    msg = "Unsupported repository backend configured."
    raise ValueError(msg)


__all__ = [
    "create_repository",
    "create_vault",
    "ensure_credential_service",
    "ensure_file_vault_key",
    "settings_value",
]
