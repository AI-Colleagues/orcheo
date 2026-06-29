"""Helpers for reading and caching Dynaconf settings."""

from __future__ import annotations
from functools import lru_cache
from dynaconf import Dynaconf
from pydantic import ValidationError
from orcheo.config.app_settings import AppSettings
from orcheo.config.chatkit_rate_limit_settings import ChatKitRateLimitSettings
from orcheo.config.defaults import _DEFAULTS
from orcheo.config.vault_settings import VaultSettings


def _build_loader() -> Dynaconf:
    """Create a Dynaconf loader wired to environment variables only."""
    return Dynaconf(
        envvar_prefix="ORCHEO",
        settings_files=[],  # No config files, env vars only
        load_dotenv=True,
        environments=False,
    )


def _normalize_settings(source: Dynaconf) -> Dynaconf:
    """Validate and fill defaults on the raw Dynaconf settings."""
    try:
        rate_limits = ChatKitRateLimitSettings.from_mapping(source)
        settings = AppSettings(
            checkpoint_backend=source.get("CHECKPOINT_BACKEND"),
            graph_store_backend=source.get(
                "GRAPH_STORE_BACKEND", _DEFAULTS["GRAPH_STORE_BACKEND"]
            ),
            repository_backend=source.get(
                "REPOSITORY_BACKEND", _DEFAULTS["REPOSITORY_BACKEND"]
            ),
            workspace_backend=source.get(
                "WORKSPACE_BACKEND", _DEFAULTS["WORKSPACE_BACKEND"]
            ),
            chatkit_backend=source.get("CHATKIT_BACKEND", _DEFAULTS["CHATKIT_BACKEND"]),
            chatkit_storage_path=source.get(
                "CHATKIT_STORAGE_PATH", _DEFAULTS["CHATKIT_STORAGE_PATH"]
            ),
            chatkit_attachment_blob_backend=source.get(
                "CHATKIT_ATTACHMENT_BLOB_BACKEND",
                _DEFAULTS["CHATKIT_ATTACHMENT_BLOB_BACKEND"],
            ),
            chatkit_orphan_cutoff_hours=source.get(
                "CHATKIT_ORPHAN_CUTOFF_HOURS",
                _DEFAULTS["CHATKIT_ORPHAN_CUTOFF_HOURS"],
            ),
            chatkit_s3_bucket=source.get("CHATKIT_S3_BUCKET"),
            chatkit_s3_endpoint_url=source.get("CHATKIT_S3_ENDPOINT_URL"),
            chatkit_s3_region=source.get("CHATKIT_S3_REGION"),
            chatkit_s3_access_key_id=source.get("CHATKIT_S3_ACCESS_KEY_ID"),
            chatkit_s3_secret_access_key=source.get("CHATKIT_S3_SECRET_ACCESS_KEY"),
            studio_url=source.get("STUDIO_URL", _DEFAULTS["STUDIO_URL"]),
            chatkit_max_upload_size_bytes=source.get(
                "CHATKIT_MAX_UPLOAD_SIZE_BYTES",
                _DEFAULTS["CHATKIT_MAX_UPLOAD_SIZE_BYTES"],
            ),
            chatkit_retention_days=source.get(
                "CHATKIT_RETENTION_DAYS", _DEFAULTS["CHATKIT_RETENTION_DAYS"]
            ),
            postgres_dsn=source.get("POSTGRES_DSN"),
            postgres_pool_min_size=source.get(
                "POSTGRES_POOL_MIN_SIZE", _DEFAULTS["POSTGRES_POOL_MIN_SIZE"]
            ),
            postgres_pool_max_size=source.get(
                "POSTGRES_POOL_MAX_SIZE", _DEFAULTS["POSTGRES_POOL_MAX_SIZE"]
            ),
            postgres_pool_timeout=source.get(
                "POSTGRES_POOL_TIMEOUT", _DEFAULTS["POSTGRES_POOL_TIMEOUT"]
            ),
            postgres_pool_max_idle=source.get(
                "POSTGRES_POOL_MAX_IDLE", _DEFAULTS["POSTGRES_POOL_MAX_IDLE"]
            ),
            host=source.get("HOST", _DEFAULTS["HOST"]),
            port=source.get("PORT", _DEFAULTS["PORT"]),
            vault=VaultSettings(
                backend=source.get("VAULT_BACKEND", _DEFAULTS["VAULT_BACKEND"]),
                encryption_key=source.get("VAULT_ENCRYPTION_KEY"),
                aws_region=source.get("VAULT_AWS_REGION"),
                aws_kms_key_id=source.get("VAULT_AWS_KMS_KEY_ID"),
                token_ttl_seconds=source.get(
                    "VAULT_TOKEN_TTL_SECONDS", _DEFAULTS["VAULT_TOKEN_TTL_SECONDS"]
                ),
            ),
            chatkit_rate_limits=rate_limits,
            chatkit_widget_types=source.get(
                "CHATKIT_WIDGET_TYPES", _DEFAULTS["CHATKIT_WIDGET_TYPES"]
            ),
            chatkit_widget_action_types=source.get(
                "CHATKIT_WIDGET_ACTION_TYPES",
                _DEFAULTS["CHATKIT_WIDGET_ACTION_TYPES"],
            ),
            tracing_exporter=source.get(
                "TRACING_EXPORTER", _DEFAULTS["TRACING_EXPORTER"]
            ),
            tracing_endpoint=source.get("TRACING_ENDPOINT"),
            tracing_service_name=source.get(
                "TRACING_SERVICE_NAME", _DEFAULTS["TRACING_SERVICE_NAME"]
            ),
            tracing_sample_ratio=source.get(
                "TRACING_SAMPLE_RATIO", _DEFAULTS["TRACING_SAMPLE_RATIO"]
            ),
            tracing_insecure=source.get(
                "TRACING_INSECURE", _DEFAULTS["TRACING_INSECURE"]
            ),
            tracing_high_token_threshold=source.get(
                "TRACING_HIGH_TOKEN_THRESHOLD",
                _DEFAULTS["TRACING_HIGH_TOKEN_THRESHOLD"],
            ),
            tracing_preview_max_length=source.get(
                "TRACING_PREVIEW_MAX_LENGTH",
                _DEFAULTS["TRACING_PREVIEW_MAX_LENGTH"],
            ),
            workflow_definition_mode=source.get(
                "WORKFLOW_DEFINITION_MODE",
                _DEFAULTS["WORKFLOW_DEFINITION_MODE"],
            ),
        )
    except ValidationError as exc:  # pragma: no cover - defensive
        raise ValueError(str(exc)) from exc

    normalized = Dynaconf(
        envvar_prefix="ORCHEO",
        settings_files=[],
        load_dotenv=False,
        environments=False,
    )
    normalized.set("CHECKPOINT_BACKEND", settings.checkpoint_backend)
    normalized.set("GRAPH_STORE_BACKEND", settings.graph_store_backend)
    normalized.set("REPOSITORY_BACKEND", settings.repository_backend)
    normalized.set("WORKSPACE_BACKEND", settings.workspace_backend)
    normalized.set("CHATKIT_BACKEND", settings.chatkit_backend)
    normalized.set("CHATKIT_STORAGE_PATH", settings.chatkit_storage_path)
    normalized.set(
        "CHATKIT_ATTACHMENT_BLOB_BACKEND", settings.chatkit_attachment_blob_backend
    )
    normalized.set("CHATKIT_ORPHAN_CUTOFF_HOURS", settings.chatkit_orphan_cutoff_hours)
    normalized.set("CHATKIT_S3_BUCKET", settings.chatkit_s3_bucket)
    normalized.set("CHATKIT_S3_ENDPOINT_URL", settings.chatkit_s3_endpoint_url)
    normalized.set("CHATKIT_S3_REGION", settings.chatkit_s3_region)
    normalized.set("CHATKIT_S3_ACCESS_KEY_ID", settings.chatkit_s3_access_key_id)
    normalized.set(
        "CHATKIT_S3_SECRET_ACCESS_KEY", settings.chatkit_s3_secret_access_key
    )
    normalized.set("STUDIO_URL", settings.studio_url)
    normalized.set(
        "CHATKIT_MAX_UPLOAD_SIZE_BYTES", settings.chatkit_max_upload_size_bytes
    )
    normalized.set("CHATKIT_RETENTION_DAYS", settings.chatkit_retention_days)
    normalized.set("CHATKIT_WIDGET_TYPES", sorted(settings.chatkit_widget_types))
    normalized.set(
        "CHATKIT_WIDGET_ACTION_TYPES", sorted(settings.chatkit_widget_action_types)
    )
    normalized.set("POSTGRES_DSN", settings.postgres_dsn)
    normalized.set("POSTGRES_POOL_MIN_SIZE", settings.postgres_pool_min_size)
    normalized.set("POSTGRES_POOL_MAX_SIZE", settings.postgres_pool_max_size)
    normalized.set("POSTGRES_POOL_TIMEOUT", settings.postgres_pool_timeout)
    normalized.set("POSTGRES_POOL_MAX_IDLE", settings.postgres_pool_max_idle)
    normalized.set("HOST", settings.host)
    normalized.set("PORT", settings.port)
    normalized.set("VAULT_BACKEND", settings.vault.backend)
    normalized.set("VAULT_ENCRYPTION_KEY", settings.vault.encryption_key)
    normalized.set("VAULT_AWS_REGION", settings.vault.aws_region)
    normalized.set("VAULT_AWS_KMS_KEY_ID", settings.vault.aws_kms_key_id)
    normalized.set("VAULT_TOKEN_TTL_SECONDS", settings.vault.token_ttl_seconds)
    normalized.set("CHATKIT_RATE_LIMITS", settings.chatkit_rate_limits.model_dump())
    normalized.set("TRACING_EXPORTER", settings.tracing_exporter)
    normalized.set("TRACING_ENDPOINT", settings.tracing_endpoint)
    normalized.set("TRACING_SERVICE_NAME", settings.tracing_service_name)
    normalized.set("TRACING_SAMPLE_RATIO", settings.tracing_sample_ratio)
    normalized.set("TRACING_INSECURE", settings.tracing_insecure)
    normalized.set(
        "TRACING_HIGH_TOKEN_THRESHOLD", settings.tracing_high_token_threshold
    )
    normalized.set("TRACING_PREVIEW_MAX_LENGTH", settings.tracing_preview_max_length)
    normalized.set("WORKFLOW_DEFINITION_MODE", settings.workflow_definition_mode)

    return normalized


@lru_cache(maxsize=1)
def _load_settings() -> Dynaconf:
    """Load settings once and cache the normalized Dynaconf instance."""
    return _normalize_settings(_build_loader())


def get_settings(*, refresh: bool = False) -> Dynaconf:
    """Return the cached Dynaconf settings, reloading them if requested."""
    if refresh:
        _load_settings.cache_clear()
    return _load_settings()


__all__ = ["_build_loader", "_normalize_settings", "_load_settings", "get_settings"]
