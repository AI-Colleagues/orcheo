"""Application-level configuration models."""

from __future__ import annotations
from pathlib import Path
from typing import cast
from pydantic import BaseModel, Field, field_validator, model_validator
from orcheo.config.chatkit_rate_limit_settings import ChatKitRateLimitSettings
from orcheo.config.defaults import _DEFAULTS
from orcheo.config.types import (
    ChatKitBackend,
    CheckpointBackend,
    GraphStoreBackend,
    RepositoryBackend,
    WorkspaceBackend,
)
from orcheo.config.vault_settings import VaultSettings
from orcheo.config.workspace_settings import MultiWorkspaceSettings


class AppSettings(BaseModel):
    """Validated application runtime settings."""

    checkpoint_backend: CheckpointBackend = Field(
        default=cast(CheckpointBackend, _DEFAULTS["CHECKPOINT_BACKEND"])
    )
    sqlite_path: str = Field(default=cast(str, _DEFAULTS["SQLITE_PATH"]))
    graph_store_backend: GraphStoreBackend = Field(
        default=cast(GraphStoreBackend, _DEFAULTS["GRAPH_STORE_BACKEND"])
    )
    graph_store_sqlite_path: str = Field(
        default=cast(str, _DEFAULTS["GRAPH_STORE_SQLITE_PATH"])
    )
    repository_backend: RepositoryBackend = Field(
        default=cast(RepositoryBackend, _DEFAULTS["REPOSITORY_BACKEND"])
    )
    repository_sqlite_path: str = Field(
        default=cast(str, _DEFAULTS["REPOSITORY_SQLITE_PATH"])
    )
    workspace_backend: WorkspaceBackend = Field(
        default=cast(WorkspaceBackend, _DEFAULTS["WORKSPACE_BACKEND"])
    )
    workspace_sqlite_path: str = Field(
        default=cast(str, _DEFAULTS["WORKSPACE_SQLITE_PATH"])
    )
    chatkit_backend: ChatKitBackend = Field(
        default=cast(ChatKitBackend, _DEFAULTS["CHATKIT_BACKEND"])
    )
    chatkit_sqlite_path: str = Field(
        default=cast(str, _DEFAULTS["CHATKIT_SQLITE_PATH"])
    )
    chatkit_storage_path: str = Field(
        default=cast(str, _DEFAULTS["CHATKIT_STORAGE_PATH"])
    )
    chatkit_public_base_url: str | None = None
    chatkit_max_upload_size_bytes: int = Field(
        default=cast(int, _DEFAULTS["CHATKIT_MAX_UPLOAD_SIZE_BYTES"]), gt=0
    )
    chatkit_retention_days: int = Field(
        default=cast(int, _DEFAULTS["CHATKIT_RETENTION_DAYS"]), gt=0
    )
    chatkit_rate_limits: ChatKitRateLimitSettings = Field(
        default_factory=ChatKitRateLimitSettings
    )
    chatkit_widget_types: set[str] = Field(
        default_factory=lambda: set(cast(list[str], _DEFAULTS["CHATKIT_WIDGET_TYPES"]))
    )
    chatkit_widget_action_types: set[str] = Field(
        default_factory=lambda: set(
            cast(list[str], _DEFAULTS["CHATKIT_WIDGET_ACTION_TYPES"])
        )
    )
    postgres_dsn: str | None = None
    postgres_pool_min_size: int = Field(
        default=cast(int, _DEFAULTS["POSTGRES_POOL_MIN_SIZE"]), ge=1
    )
    postgres_pool_max_size: int = Field(
        default=cast(int, _DEFAULTS["POSTGRES_POOL_MAX_SIZE"]), ge=1
    )
    postgres_pool_timeout: float = Field(
        default=cast(float, _DEFAULTS["POSTGRES_POOL_TIMEOUT"]), gt=0.0
    )
    postgres_pool_max_idle: float = Field(
        default=cast(float, _DEFAULTS["POSTGRES_POOL_MAX_IDLE"]), gt=0.0
    )
    host: str = Field(default=cast(str, _DEFAULTS["HOST"]))
    port: int = Field(default=cast(int, _DEFAULTS["PORT"]))
    vault: VaultSettings = Field(default_factory=VaultSettings)
    multi_workspace: MultiWorkspaceSettings = Field(
        default_factory=MultiWorkspaceSettings
    )
    tracing_exporter: str = Field(default=cast(str, _DEFAULTS["TRACING_EXPORTER"]))
    tracing_endpoint: str | None = None
    tracing_service_name: str = Field(
        default=cast(str, _DEFAULTS["TRACING_SERVICE_NAME"])
    )
    tracing_sample_ratio: float = Field(
        default=cast(float, _DEFAULTS["TRACING_SAMPLE_RATIO"]),
        ge=0.0,
        le=1.0,
    )
    tracing_insecure: bool = Field(default=cast(bool, _DEFAULTS["TRACING_INSECURE"]))
    tracing_high_token_threshold: int = Field(
        default=cast(int, _DEFAULTS["TRACING_HIGH_TOKEN_THRESHOLD"]),
        ge=1,
    )
    tracing_preview_max_length: int = Field(
        default=cast(int, _DEFAULTS["TRACING_PREVIEW_MAX_LENGTH"]),
        ge=16,
    )

    @staticmethod
    def _coerce_widget_set(value: object, default_key: str) -> set[str]:
        defaults = set(cast(list[str], _DEFAULTS[default_key]))
        if value is None:
            return defaults
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return set(parts) or defaults
        if isinstance(value, set | frozenset | list | tuple):
            coerced = {str(entry).strip() for entry in value if str(entry).strip()}
            return coerced or defaults
        return defaults

    @field_validator("checkpoint_backend", mode="before")
    @classmethod
    def _coerce_checkpoint_backend(cls, value: object) -> CheckpointBackend:
        candidate = (
            cast(str, value).lower()
            if value is not None
            else cast(str, _DEFAULTS["CHECKPOINT_BACKEND"])
        )
        if candidate not in {"sqlite", "postgres"}:
            msg = "ORCHEO_CHECKPOINT_BACKEND must be either 'sqlite' or 'postgres'."
            raise ValueError(msg)
        return cast(CheckpointBackend, candidate)

    @field_validator("repository_backend", mode="before")
    @classmethod
    def _coerce_repository_backend(cls, value: object) -> RepositoryBackend:
        candidate = (
            cast(str, value).lower()
            if value is not None
            else cast(str, _DEFAULTS["REPOSITORY_BACKEND"])
        )
        if candidate not in {"inmemory", "sqlite", "postgres"}:
            msg = (
                "ORCHEO_REPOSITORY_BACKEND must be 'inmemory', 'sqlite', or 'postgres'."
            )
            raise ValueError(msg)
        return cast(RepositoryBackend, candidate)

    @field_validator("workspace_backend", mode="before")
    @classmethod
    def _coerce_workspace_backend(cls, value: object) -> WorkspaceBackend:
        candidate = (
            cast(str, value).lower()
            if value is not None
            else cast(str, _DEFAULTS["WORKSPACE_BACKEND"])
        )
        if candidate not in {"inmemory", "sqlite", "postgres"}:
            msg = (
                "ORCHEO_WORKSPACE_BACKEND must be 'inmemory', 'sqlite', or 'postgres'."
            )
            raise ValueError(msg)
        return cast(WorkspaceBackend, candidate)

    @field_validator("graph_store_backend", mode="before")
    @classmethod
    def _coerce_graph_store_backend(cls, value: object) -> GraphStoreBackend:
        candidate = (
            cast(str, value).lower()
            if value is not None
            else cast(str, _DEFAULTS["GRAPH_STORE_BACKEND"])
        )
        if candidate not in {"sqlite", "postgres"}:
            msg = "ORCHEO_GRAPH_STORE_BACKEND must be either 'sqlite' or 'postgres'."
            raise ValueError(msg)
        return cast(GraphStoreBackend, candidate)

    @field_validator("chatkit_backend", mode="before")
    @classmethod
    def _coerce_chatkit_backend(cls, value: object) -> ChatKitBackend:
        candidate = (
            cast(str, value).lower()
            if value is not None
            else cast(str, _DEFAULTS["CHATKIT_BACKEND"])
        )
        if candidate not in {"sqlite", "postgres"}:
            msg = "ORCHEO_CHATKIT_BACKEND must be either 'sqlite' or 'postgres'."
            raise ValueError(msg)
        return cast(ChatKitBackend, candidate)

    @field_validator("sqlite_path", "host", mode="before")
    @classmethod
    def _coerce_str(cls, value: object) -> str:
        return str(value) if value is not None else ""

    @field_validator("repository_sqlite_path", mode="before")
    @classmethod
    def _coerce_repo_sqlite_path(cls, value: object) -> str:
        return str(value) if value is not None else ""

    @field_validator("workspace_sqlite_path", mode="before")
    @classmethod
    def _coerce_workspace_sqlite_path(cls, value: object) -> str:
        return str(value) if value is not None else ""

    @field_validator("graph_store_sqlite_path", mode="before")
    @classmethod
    def _coerce_graph_store_sqlite_path(cls, value: object) -> str:
        return str(value) if value is not None else ""

    @field_validator("chatkit_sqlite_path", mode="before")
    @classmethod
    def _coerce_chatkit_sqlite_path(cls, value: object) -> str:
        return str(value) if value is not None else ""

    @field_validator("chatkit_storage_path", mode="before")
    @classmethod
    def _coerce_chatkit_storage_path(cls, value: object) -> str:
        return str(value) if value is not None else ""

    @field_validator("chatkit_public_base_url", mode="before")
    @classmethod
    def _coerce_chatkit_public_base_url(cls, value: object) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("chatkit_max_upload_size_bytes", mode="before")
    @classmethod
    def _coerce_chatkit_max_upload_size_bytes(cls, value: object) -> int:
        candidate_obj = (
            value if value is not None else _DEFAULTS["CHATKIT_MAX_UPLOAD_SIZE_BYTES"]
        )
        if isinstance(candidate_obj, int | float):
            return int(candidate_obj)
        try:
            return int(str(candidate_obj))
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            msg = "ORCHEO_CHATKIT_MAX_UPLOAD_SIZE_BYTES must be an integer."
            raise ValueError(msg) from exc

    @field_validator("chatkit_retention_days", mode="before")
    @classmethod
    def _coerce_chatkit_retention(cls, value: object) -> int:
        candidate_obj = (
            value if value is not None else _DEFAULTS["CHATKIT_RETENTION_DAYS"]
        )
        if isinstance(candidate_obj, int):
            return candidate_obj
        try:
            return int(str(candidate_obj))
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            msg = "ORCHEO_CHATKIT_RETENTION_DAYS must be an integer."
            raise ValueError(msg) from exc

    @field_validator("chatkit_widget_types", mode="before")
    @classmethod
    def _coerce_widget_types(cls, value: object) -> set[str]:
        return cls._coerce_widget_set(value, "CHATKIT_WIDGET_TYPES")

    @field_validator("chatkit_widget_action_types", mode="before")
    @classmethod
    def _coerce_widget_action_types(cls, value: object) -> set[str]:
        return cls._coerce_widget_set(value, "CHATKIT_WIDGET_ACTION_TYPES")

    @staticmethod
    def _coerce_postgres_pool_int(value: object, default_key: str) -> int:
        if value is None:
            return cast(int, _DEFAULTS[default_key])
        if isinstance(value, int):
            return value
        try:
            return int(str(value))
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            msg = "PostgreSQL pool size must be a positive integer."
            raise ValueError(msg) from exc

    @staticmethod
    def _coerce_postgres_pool_float(value: object, default_key: str) -> float:
        if value is None:
            return cast(float, _DEFAULTS[default_key])
        if isinstance(value, int | float):
            return float(value)
        try:
            return float(str(value))
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            msg = "PostgreSQL pool timeout must be a positive number."
            raise ValueError(msg) from exc

    @field_validator("postgres_pool_min_size", mode="before")
    @classmethod
    def _coerce_postgres_pool_min_size(cls, value: object) -> int:
        return cls._coerce_postgres_pool_int(value, "POSTGRES_POOL_MIN_SIZE")

    @field_validator("postgres_pool_max_size", mode="before")
    @classmethod
    def _coerce_postgres_pool_max_size(cls, value: object) -> int:
        return cls._coerce_postgres_pool_int(value, "POSTGRES_POOL_MAX_SIZE")

    @field_validator("postgres_pool_timeout", mode="before")
    @classmethod
    def _coerce_postgres_pool_timeout(cls, value: object) -> float:
        return cls._coerce_postgres_pool_float(value, "POSTGRES_POOL_TIMEOUT")

    @field_validator("postgres_pool_max_idle", mode="before")
    @classmethod
    def _coerce_postgres_pool_max_idle(cls, value: object) -> float:
        return cls._coerce_postgres_pool_float(value, "POSTGRES_POOL_MAX_IDLE")

    @field_validator("port", mode="before")
    @classmethod
    def _parse_port(cls, value: object) -> int:
        candidate_obj = value if value is not None else _DEFAULTS["PORT"]
        candidate: int | str
        if isinstance(candidate_obj, int | str):
            candidate = candidate_obj
        else:
            candidate = str(candidate_obj)
        try:
            return int(candidate)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError("ORCHEO_PORT must be an integer.") from exc

    @field_validator("tracing_exporter", mode="before")
    @classmethod
    def _coerce_tracing_exporter(cls, value: object) -> str:
        candidate_source = value if value is not None else _DEFAULTS["TRACING_EXPORTER"]
        candidate = str(candidate_source).lower()
        allowed = {"none", "otlp", "console"}
        if candidate not in allowed:
            msg = "ORCHEO_TRACING_EXPORTER must be one of 'none', 'otlp', or 'console'."
            raise ValueError(msg)
        return candidate

    @field_validator("tracing_endpoint", mode="before")
    @classmethod
    def _coerce_tracing_endpoint(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value)

    @field_validator("tracing_service_name", mode="before")
    @classmethod
    def _coerce_tracing_service_name(cls, value: object) -> str:
        candidate = value if value is not None else _DEFAULTS["TRACING_SERVICE_NAME"]
        return str(candidate)

    @field_validator("tracing_sample_ratio", mode="before")
    @classmethod
    def _coerce_tracing_sample_ratio(cls, value: object) -> float:
        candidate_obj = (
            value if value is not None else _DEFAULTS["TRACING_SAMPLE_RATIO"]
        )
        try:
            if isinstance(candidate_obj, int | float | str):
                candidate = float(candidate_obj)
            else:
                candidate = float(str(candidate_obj))
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            msg = "ORCHEO_TRACING_SAMPLE_RATIO must be a float between 0 and 1."
            raise ValueError(msg) from exc
        if not 0.0 <= candidate <= 1.0:
            msg = "ORCHEO_TRACING_SAMPLE_RATIO must be between 0 and 1."
            raise ValueError(msg)
        return candidate

    @field_validator("tracing_insecure", mode="before")
    @classmethod
    def _coerce_tracing_insecure(cls, value: object) -> bool:
        candidate_obj = value if value is not None else _DEFAULTS["TRACING_INSECURE"]
        if isinstance(candidate_obj, bool):
            return candidate_obj
        if isinstance(candidate_obj, str):
            lowered = candidate_obj.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return bool(candidate_obj)

    @field_validator("tracing_high_token_threshold", mode="before")
    @classmethod
    def _coerce_tracing_high_token_threshold(cls, value: object) -> int:
        candidate_obj = (
            value if value is not None else _DEFAULTS["TRACING_HIGH_TOKEN_THRESHOLD"]
        )
        if isinstance(candidate_obj, bool):  # pragma: no cover - defensive
            return int(candidate_obj)
        if isinstance(candidate_obj, int | float):
            return int(candidate_obj)
        try:
            return int(str(candidate_obj))
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            msg = "ORCHEO_TRACING_HIGH_TOKEN_THRESHOLD must be a positive integer."
            raise ValueError(msg) from exc

    @field_validator("tracing_preview_max_length", mode="before")
    @classmethod
    def _coerce_tracing_preview_max_length(cls, value: object) -> int:
        candidate_obj = (
            value if value is not None else _DEFAULTS["TRACING_PREVIEW_MAX_LENGTH"]
        )
        if isinstance(candidate_obj, bool):  # pragma: no cover - defensive
            return int(candidate_obj)
        if isinstance(candidate_obj, int | float):
            return int(candidate_obj)
        try:
            return int(str(candidate_obj))
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            msg = "ORCHEO_TRACING_PREVIEW_MAX_LENGTH must be a positive integer."
            raise ValueError(msg) from exc

    def _validate_postgres_dsn(self) -> None:
        uses_postgres = {
            self.checkpoint_backend,
            self.graph_store_backend,
            self.repository_backend,
            self.workspace_backend,
            self.chatkit_backend,
            self.vault.backend,
        }
        if "postgres" in uses_postgres:
            if not self.postgres_dsn:
                msg = "ORCHEO_POSTGRES_DSN must be set when using the postgres backend."
                raise ValueError(msg)
            self.postgres_dsn = str(self.postgres_dsn)
            return
        self.postgres_dsn = None

    def _apply_runtime_defaults(self) -> None:
        self.sqlite_path = self.sqlite_path or cast(str, _DEFAULTS["SQLITE_PATH"])
        self.graph_store_sqlite_path = self.graph_store_sqlite_path or cast(
            str, _DEFAULTS["GRAPH_STORE_SQLITE_PATH"]
        )
        self.repository_sqlite_path = self.repository_sqlite_path or cast(
            str, _DEFAULTS["REPOSITORY_SQLITE_PATH"]
        )
        self.chatkit_sqlite_path = self.chatkit_sqlite_path or cast(
            str, _DEFAULTS["CHATKIT_SQLITE_PATH"]
        )
        self.chatkit_storage_path = self.chatkit_storage_path or cast(
            str, _DEFAULTS["CHATKIT_STORAGE_PATH"]
        )
        if self.chatkit_retention_days <= 0:  # pragma: no cover - defensive
            self.chatkit_retention_days = cast(int, _DEFAULTS["CHATKIT_RETENTION_DAYS"])
        if not isinstance(self.chatkit_rate_limits, ChatKitRateLimitSettings):
            self.chatkit_rate_limits = ChatKitRateLimitSettings()
        self.host = self.host or cast(str, _DEFAULTS["HOST"])
        if not self.tracing_exporter:
            self.tracing_exporter = cast(str, _DEFAULTS["TRACING_EXPORTER"])
        if not self.tracing_service_name:
            self.tracing_service_name = cast(str, _DEFAULTS["TRACING_SERVICE_NAME"])
        if self.tracing_high_token_threshold <= 0:
            self.tracing_high_token_threshold = cast(
                int, _DEFAULTS["TRACING_HIGH_TOKEN_THRESHOLD"]
            )
        if self.tracing_preview_max_length <= 0:
            self.tracing_preview_max_length = cast(
                int, _DEFAULTS["TRACING_PREVIEW_MAX_LENGTH"]
            )

    def _validate_graph_store_path(self) -> None:
        if self.graph_store_backend != "sqlite":
            return
        if self.graph_store_sqlite_path.strip().startswith("~"):
            msg = (
                "ORCHEO_GRAPH_STORE_SQLITE_PATH must be an absolute path and "
                "must not start with '~'."
            )
            raise ValueError(msg)
        graph_store_path = Path(self.graph_store_sqlite_path)
        if not graph_store_path.is_absolute():
            msg = "ORCHEO_GRAPH_STORE_SQLITE_PATH must be an absolute filesystem path."
            raise ValueError(msg)
        self.graph_store_sqlite_path = str(graph_store_path.resolve(strict=False))

    @model_validator(mode="after")
    def _validate_postgres_requirements(self) -> AppSettings:
        self._validate_postgres_dsn()
        self._apply_runtime_defaults()
        self._validate_graph_store_path()
        return self


__all__ = ["AppSettings"]
