"""Tests for configuration helpers."""

from collections.abc import Generator
import pytest
from dynaconf import Dynaconf
from orcheo import config
from orcheo.config import loader as config_loader
from orcheo.config.app_settings import AppSettings
from orcheo.config.chatkit_rate_limit_settings import ChatKitRateLimitSettings
from orcheo.config.defaults import _DEFAULTS
from orcheo.config.vault_settings import VaultSettings


def _build_loader_without_dotenv() -> Dynaconf:
    """Create a Dynaconf loader that doesn't load from .env files."""
    return Dynaconf(
        envvar_prefix="ORCHEO",
        settings_files=[],
        load_dotenv=False,
        environments=False,
    )


@pytest.fixture()
def no_dotenv_loader(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Patch the config loader to not load .env files."""
    monkeypatch.setattr(config_loader, "_build_loader", _build_loader_without_dotenv)
    yield


def test_settings_defaults(
    monkeypatch: pytest.MonkeyPatch, no_dotenv_loader: None
) -> None:
    """Default settings fall back to PostgreSQL-backed persistence and localhost."""

    monkeypatch.delenv("ORCHEO_CHECKPOINT_BACKEND", raising=False)
    monkeypatch.delenv("ORCHEO_CHATKIT_BACKEND", raising=False)
    monkeypatch.delenv("ORCHEO_WORKSPACE_BACKEND", raising=False)
    monkeypatch.delenv("ORCHEO_HOST", raising=False)
    monkeypatch.delenv("ORCHEO_PORT", raising=False)
    monkeypatch.delenv("ORCHEO_VAULT_BACKEND", raising=False)
    monkeypatch.delenv("ORCHEO_VAULT_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ORCHEO_VAULT_AWS_REGION", raising=False)
    monkeypatch.delenv("ORCHEO_VAULT_AWS_KMS_KEY_ID", raising=False)
    monkeypatch.delenv("ORCHEO_VAULT_TOKEN_TTL_SECONDS", raising=False)
    monkeypatch.delenv("ORCHEO_TRACING_HIGH_TOKEN_THRESHOLD", raising=False)
    monkeypatch.delenv("ORCHEO_TRACING_PREVIEW_MAX_LENGTH", raising=False)
    monkeypatch.setenv("ORCHEO_VAULT_ENCRYPTION_KEY", "test-vault-encryption-key")

    settings = config.get_settings(refresh=True)

    assert settings.checkpoint_backend == "postgres"
    assert settings.graph_store_backend == "postgres"
    assert settings.chatkit_backend == "postgres"
    assert settings.repository_backend == "postgres"
    assert settings.workspace_backend == "postgres"
    assert settings.vault_backend == "postgres"
    assert settings.postgres_pool_min_size == 1
    assert settings.postgres_pool_max_size == 10
    assert settings.postgres_pool_timeout == 30.0
    assert settings.postgres_pool_max_idle == 300.0
    assert settings.host == "0.0.0.0"
    assert settings.port == 2025
    assert settings.vault_encryption_key == "test-vault-encryption-key"
    assert settings.vault_token_ttl_seconds == 3600
    assert settings.tracing_high_token_threshold == 1000
    assert settings.tracing_preview_max_length == 512


def test_settings_invalid_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid persistence backend values raise a helpful error."""

    monkeypatch.setenv("ORCHEO_CHECKPOINT_BACKEND", "invalid")

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_settings_invalid_repository_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repository backend validation enforces supported options."""

    monkeypatch.setenv("ORCHEO_REPOSITORY_BACKEND", "unsupported")

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_settings_invalid_chatkit_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChatKit backend validation enforces supported options."""

    monkeypatch.setenv("ORCHEO_CHATKIT_BACKEND", "unsupported")

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_settings_invalid_graph_store_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph store backend validation enforces supported options."""

    monkeypatch.setenv("ORCHEO_GRAPH_STORE_BACKEND", "unsupported")

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_postgres_backend_requires_dsn(
    monkeypatch: pytest.MonkeyPatch, no_dotenv_loader: None
) -> None:
    """Using Postgres without a DSN should fail fast."""

    monkeypatch.setenv("ORCHEO_CHECKPOINT_BACKEND", "postgres")
    monkeypatch.delenv("ORCHEO_POSTGRES_DSN", raising=False)

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_postgres_repository_requires_dsn(
    monkeypatch: pytest.MonkeyPatch, no_dotenv_loader: None
) -> None:
    """Repository backends also require a DSN when Postgres is selected."""

    monkeypatch.setenv("ORCHEO_REPOSITORY_BACKEND", "postgres")
    monkeypatch.delenv("ORCHEO_POSTGRES_DSN", raising=False)

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)

    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")
    settings = config.get_settings(refresh=True)

    assert settings.repository_backend == "postgres"
    assert settings.postgres_dsn == "postgresql://example"


def test_postgres_workspace_requires_dsn(
    monkeypatch: pytest.MonkeyPatch, no_dotenv_loader: None
) -> None:
    """Workspace backends also require a DSN when Postgres is selected."""

    monkeypatch.setenv("ORCHEO_WORKSPACE_BACKEND", "postgres")
    monkeypatch.delenv("ORCHEO_POSTGRES_DSN", raising=False)

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)

    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")
    settings = config.get_settings(refresh=True)

    assert settings.workspace_backend == "postgres"
    assert settings.postgres_dsn == "postgresql://example"


def test_postgres_chatkit_requires_dsn(
    monkeypatch: pytest.MonkeyPatch, no_dotenv_loader: None
) -> None:
    """ChatKit postgres backend requires a DSN to be configured."""

    monkeypatch.setenv("ORCHEO_CHATKIT_BACKEND", "postgres")
    monkeypatch.delenv("ORCHEO_POSTGRES_DSN", raising=False)

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)

    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")
    settings = config.get_settings(refresh=True)

    assert settings.chatkit_backend == "postgres"
    assert settings.postgres_dsn == "postgresql://example"


def test_postgres_graph_store_requires_dsn(
    monkeypatch: pytest.MonkeyPatch, no_dotenv_loader: None
) -> None:
    """Graph-store postgres backend also requires a DSN."""

    monkeypatch.setenv("ORCHEO_GRAPH_STORE_BACKEND", "postgres")
    monkeypatch.delenv("ORCHEO_POSTGRES_DSN", raising=False)

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)

    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")
    settings = config.get_settings(refresh=True)
    assert settings.graph_store_backend == "postgres"
    assert settings.postgres_dsn == "postgresql://example"


def test_postgres_pool_settings_are_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Postgres pool settings should be available on normalized settings."""

    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_MAX_SIZE", "9")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_TIMEOUT", "12")
    monkeypatch.setenv("ORCHEO_POSTGRES_POOL_MAX_IDLE", "60")

    settings = config.get_settings(refresh=True)

    assert settings.postgres_pool_min_size == 2
    assert settings.postgres_pool_max_size == 9
    assert settings.postgres_pool_timeout == 12.0
    assert settings.postgres_pool_max_idle == 60.0


def test_normalize_backend_none() -> None:
    """Explicit `None` backend values should fall back to defaults."""

    source = Dynaconf(settings_files=[], load_dotenv=False, environments=False)
    source.set("CHECKPOINT_BACKEND", None)
    source.set("POSTGRES_DSN", "postgresql://example")
    source.set("VAULT_ENCRYPTION_KEY", "test-vault-encryption-key")

    normalized = config._normalize_settings(source)

    assert normalized.checkpoint_backend == "postgres"


def test_get_settings_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_settings refresh flag should reload cached values."""

    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://initial")
    settings = config.get_settings(refresh=True)
    assert settings.postgres_dsn == "postgresql://initial"

    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://updated")
    refreshed = config.get_settings(refresh=True)
    assert refreshed.postgres_dsn == "postgresql://updated"


def test_tracing_settings_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tracing preview and threshold settings should be configurable."""

    monkeypatch.setenv("ORCHEO_TRACING_HIGH_TOKEN_THRESHOLD", "250")
    monkeypatch.setenv("ORCHEO_TRACING_PREVIEW_MAX_LENGTH", "256")

    settings = config.get_settings(refresh=True)

    assert settings.tracing_high_token_threshold == 250
    assert settings.tracing_preview_max_length == 256

    monkeypatch.delenv("ORCHEO_TRACING_HIGH_TOKEN_THRESHOLD", raising=False)
    monkeypatch.delenv("ORCHEO_TRACING_PREVIEW_MAX_LENGTH", raising=False)
    config.get_settings(refresh=True)


def test_invalid_vault_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vault backend must match supported options."""

    monkeypatch.setenv("ORCHEO_VAULT_BACKEND", "unsupported")

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_postgres_vault_requires_dsn(
    monkeypatch: pytest.MonkeyPatch, no_dotenv_loader: None
) -> None:
    """Postgres vaults require a Postgres DSN."""

    monkeypatch.setenv("ORCHEO_VAULT_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_VAULT_ENCRYPTION_KEY", "enc-key")
    monkeypatch.delenv("ORCHEO_POSTGRES_DSN", raising=False)

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_postgres_vault_requires_encryption_key(
    monkeypatch: pytest.MonkeyPatch, no_dotenv_loader: None
) -> None:
    """Postgres vaults require an encryption key."""

    monkeypatch.setenv("ORCHEO_VAULT_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")
    monkeypatch.delenv("ORCHEO_VAULT_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_postgres_vault_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Postgres vaults preserve encryption and ignore file-backed settings."""

    monkeypatch.setenv("ORCHEO_VAULT_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://example")
    monkeypatch.setenv("ORCHEO_VAULT_ENCRYPTION_KEY", "enc-key")

    settings = config.get_settings(refresh=True)

    assert settings.vault_backend == "postgres"
    assert settings.vault_encryption_key == "enc-key"
    assert settings.vault_aws_region is None
    assert settings.vault_aws_kms_key_id is None
    assert settings.postgres_dsn == "postgresql://example"


def test_inmemory_vault_backend_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-memory vault backends are no longer supported."""

    monkeypatch.setenv("ORCHEO_VAULT_BACKEND", "inmemory")

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_vault_rejects_unsupported_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported vault backends should fail validation."""

    monkeypatch.setenv("ORCHEO_VAULT_BACKEND", "file")

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_vault_token_ttl_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token TTL values must be positive integers."""

    monkeypatch.setenv("ORCHEO_VAULT_TOKEN_TTL_SECONDS", "-1")

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)

    monkeypatch.setenv("ORCHEO_VAULT_TOKEN_TTL_SECONDS", "900")
    settings = config.get_settings(refresh=True)
    assert settings.vault_token_ttl_seconds == 900


def test_postgres_vault_requires_encryption_key_again(
    monkeypatch: pytest.MonkeyPatch, no_dotenv_loader: None
) -> None:
    """Postgres vaults must provide an encryption key."""

    monkeypatch.setenv("ORCHEO_VAULT_BACKEND", "postgres")
    monkeypatch.delenv("ORCHEO_VAULT_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValueError):
        config.get_settings(refresh=True)


def test_numeric_fields_accept_str_coercible_objects() -> None:
    """Port and vault TTL should coerce objects that stringify to integers."""

    class Intish:
        def __init__(self, value: int) -> None:
            self._value = value

        def __str__(self) -> str:
            return str(self._value)

    source = Dynaconf(settings_files=[], load_dotenv=False, environments=False)
    source.set("POSTGRES_DSN", "postgresql://example")
    source.set("VAULT_ENCRYPTION_KEY", "test-vault-encryption-key")
    source.set("PORT", Intish(4711))
    source.set("VAULT_TOKEN_TTL_SECONDS", Intish(1234))

    normalized = config._normalize_settings(source)

    assert normalized.port == 4711
    assert normalized.vault_token_ttl_seconds == 1234


def test_chatkit_retention_coerces_string_values() -> None:
    """Chatkit retention days should accept string representations of integers."""

    source = Dynaconf(settings_files=[], load_dotenv=False, environments=False)
    source.set("POSTGRES_DSN", "postgresql://example")
    source.set("VAULT_ENCRYPTION_KEY", "test-vault-encryption-key")
    source.set("CHATKIT_RETENTION_DAYS", "21")

    normalized = config._normalize_settings(source)

    assert normalized.chatkit_retention_days == 21


def test_chatkit_upload_size_coerces_string_values() -> None:
    """ChatKit max upload size should accept string representations of integers."""

    source = Dynaconf(settings_files=[], load_dotenv=False, environments=False)
    source.set("POSTGRES_DSN", "postgresql://example")
    source.set("VAULT_ENCRYPTION_KEY", "test-vault-encryption-key")
    source.set("CHATKIT_MAX_UPLOAD_SIZE_BYTES", "1024")

    normalized = config._normalize_settings(source)

    assert normalized.chatkit_max_upload_size_bytes == 1024


def test_chatkit_rate_limit_settings_are_loaded() -> None:
    """ChatKit rate limit configuration should surface defaults and overrides."""

    source = Dynaconf(settings_files=[], load_dotenv=False, environments=False)
    source.set("POSTGRES_DSN", "postgresql://example")
    source.set("VAULT_ENCRYPTION_KEY", "test-vault-encryption-key")
    source.set("CHATKIT_RATE_LIMIT_IP_LIMIT", "200")
    source.set("CHATKIT_RATE_LIMIT_PUBLISH_INTERVAL", "90")

    normalized = config._normalize_settings(source)
    limits = normalized.get("CHATKIT_RATE_LIMITS")

    assert limits["ip_limit"] == 200
    assert limits["publish_interval_seconds"] == 90
    assert limits["session_limit"] == 60


def test_app_settings_wraps_chatkit_rate_limit_mapping() -> None:
    """chatkit_rate_limits is coerced into ChatKitRateLimitSettings."""

    settings = AppSettings(
        chatkit_rate_limits={"unexpected": "value"},
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )

    assert isinstance(settings.chatkit_rate_limits, ChatKitRateLimitSettings)


def test_app_settings_recovers_invalid_chatkit_rate_limits() -> None:
    """Model validator should replace unexpected chatkit_rate_limits types."""

    settings = AppSettings(
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )
    settings.chatkit_rate_limits = {"unexpected": "value"}

    # Manually invoke the model validator to simulate a defensive re-run.
    validated = settings._validate_postgres_requirements()

    assert isinstance(validated.chatkit_rate_limits, ChatKitRateLimitSettings)


def test_app_settings_rejects_unknown_tracing_exporter() -> None:
    """Tracing exporter validator must reject unsupported options."""

    with pytest.raises(ValueError):
        AppSettings(
            tracing_exporter="zipkin",
            postgres_dsn="postgresql://example",
            vault=VaultSettings(encryption_key="test-vault-encryption-key"),
        )


def test_app_settings_coerces_tracing_endpoint_and_ratio() -> None:
    """Tracing endpoint and sample ratio accept loosely typed inputs."""

    class Ratio:
        def __str__(self) -> str:
            return "0.5"

    settings = AppSettings(
        tracing_endpoint=12345,
        tracing_sample_ratio=Ratio(),
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )

    assert settings.tracing_endpoint == "12345"
    assert settings.tracing_sample_ratio == 0.5


def test_app_settings_enforces_sample_ratio_range() -> None:
    """Tracing sample ratio must fall within [0, 1]."""

    with pytest.raises(ValueError):
        AppSettings(
            tracing_sample_ratio=2,
            postgres_dsn="postgresql://example",
            vault=VaultSettings(encryption_key="test-vault-encryption-key"),
        )


def test_app_settings_coerces_tracing_insecure_strings() -> None:
    """String values for tracing_insecure should be interpreted leniently."""

    settings_true = AppSettings(
        tracing_insecure="YES",
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )
    settings_false = AppSettings(
        tracing_insecure="off",
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )

    assert settings_true.tracing_insecure is True
    assert settings_false.tracing_insecure is False


def test_app_settings_coerces_tracing_insecure_with_bool_cast() -> None:
    """Non-string, non-bool values should fall back to Python's truthiness."""

    settings_truthy = AppSettings(
        tracing_insecure=5,
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )
    settings_falsey = AppSettings(
        tracing_insecure=0,
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )

    assert settings_truthy.tracing_insecure is True
    assert settings_falsey.tracing_insecure is False


def test_app_settings_tracing_insecure_handles_unknown_strings() -> None:
    """Unexpected string values should fall back to truthy evaluation."""

    settings_truthy = AppSettings(
        tracing_insecure="maybe",
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )
    settings_falsey = AppSettings(
        tracing_insecure="",
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )

    assert settings_truthy.tracing_insecure is True
    assert settings_falsey.tracing_insecure is False


def test_app_settings_coerces_thresholds_from_custom_objects() -> None:
    """Threshold validators should convert arbitrary objects via str()."""

    class Numeric:
        def __init__(self, value: str) -> None:
            self._value = value

        def __str__(self) -> str:
            return self._value

    settings = AppSettings(
        tracing_high_token_threshold=Numeric("2048"),
        tracing_preview_max_length=Numeric("1024"),
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )

    assert settings.tracing_high_token_threshold == 2048
    assert settings.tracing_preview_max_length == 1024


def test_app_settings_validator_restores_tracing_defaults() -> None:
    """Model validator should backfill tracing defaults when values unset."""

    settings = AppSettings(
        postgres_dsn="postgresql://example",
        vault=VaultSettings(encryption_key="test-vault-encryption-key"),
    )
    settings.tracing_exporter = ""
    settings.tracing_service_name = ""
    settings.tracing_high_token_threshold = 0
    settings.tracing_preview_max_length = -1

    validated = settings._validate_postgres_requirements()

    assert validated.tracing_exporter == _DEFAULTS["TRACING_EXPORTER"]
    assert validated.tracing_service_name == _DEFAULTS["TRACING_SERVICE_NAME"]
    assert (
        validated.tracing_high_token_threshold
        == _DEFAULTS["TRACING_HIGH_TOKEN_THRESHOLD"]
    )
    assert (
        validated.tracing_preview_max_length == _DEFAULTS["TRACING_PREVIEW_MAX_LENGTH"]
    )
