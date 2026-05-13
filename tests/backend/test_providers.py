"""Tests for backend provider factories."""

from __future__ import annotations
from types import SimpleNamespace
from typing import Any
import pytest
from orcheo.vault import InMemoryCredentialVault
from orcheo_backend.app import providers


class DummySettings:
    """Minimal Dynaconf-like stub using an internal mapping."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str, default: Any) -> Any:
        return self._values.get(key, default)


def test_settings_value_traverses_attr_path_when_get_missing() -> None:
    """settings_value should walk dotted attributes when get() is unavailable."""
    settings = SimpleNamespace(vault=SimpleNamespace(backend="inmemory"))

    result = providers.settings_value(
        settings,
        attr_path="vault.backend",
        env_key="VAULT_BACKEND",
        default="file",
    )

    assert result == "inmemory"


def test_settings_value_returns_default_when_attr_chain_missing() -> None:
    """settings_value should return default if the attr chain is incomplete."""
    settings = SimpleNamespace(vault={})

    result = providers.settings_value(
        settings,
        attr_path="vault.backend",
        env_key="VAULT_BACKEND",
        default="fallback",
    )

    assert result == "fallback"


def test_settings_value_without_attr_path_returns_default() -> None:
    """settings_value returns the default when attr_path is not provided."""
    settings = SimpleNamespace(vault=SimpleNamespace(backend="file"))

    result = providers.settings_value(
        settings,
        attr_path=None,
        env_key="VAULT_BACKEND",
        default="default-backend",
    )

    assert result == "default-backend"


def test_create_vault_inmemory_uses_provided_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_vault should honor the configured encryption key for inmemory backend."""

    settings = DummySettings(
        {
            "VAULT_BACKEND": "inmemory",
            "VAULT_ENCRYPTION_KEY": "override-key",
        }
    )

    captured: dict[str, str] = {}

    class FakeCipher:
        def __init__(self, *, key: str) -> None:
            captured["key"] = key

    monkeypatch.setattr(providers, "AesGcmCredentialCipher", FakeCipher)

    vault = providers.create_vault(settings)

    assert isinstance(vault, InMemoryCredentialVault)
    assert captured["key"] == "override-key"


def test_create_repository_sqlite_backend_sets_history_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_repository should configure sqlite repositories and history store."""
    settings = DummySettings(
        {
            "REPOSITORY_BACKEND": "sqlite",
            "REPOSITORY_SQLITE_PATH": "/tmp/workflows.sqlite",
        }
    )
    credential_service = object()
    history_store_ref: dict[str, object] = {}
    checkpoint_store_ref: dict[str, object] = {}
    plugin_installation_store_ref: dict[str, object] = {}

    class FakeStore:
        def __init__(self, path: str) -> None:
            self.path = path

    class FakeRepository:
        def __init__(self, path: str, *, credential_service: object) -> None:
            self.path = path
            self.credential_service = credential_service

    class FakePluginInstallationStore:
        def __init__(self, path: str) -> None:
            self.path = path

    monkeypatch.setattr(providers, "SqliteRunHistoryStore", FakeStore)
    monkeypatch.setattr(providers, "SqliteAgentensorCheckpointStore", FakeStore)
    monkeypatch.setattr(
        providers, "SqlitePluginInstallationStore", FakePluginInstallationStore
    )
    monkeypatch.setattr(providers, "SqliteWorkflowRepository", FakeRepository)

    repository = providers.create_repository(
        settings,
        credential_service=credential_service,
        history_store_ref=history_store_ref,
        checkpoint_store_ref=checkpoint_store_ref,
        plugin_installation_store_ref=plugin_installation_store_ref,
    )

    assert isinstance(repository, FakeRepository)
    assert repository.path == "/tmp/workflows.sqlite"
    assert repository.credential_service is credential_service
    assert isinstance(history_store_ref["store"], FakeStore)
    assert history_store_ref["store"].path == "/tmp/workflows.sqlite"
    assert isinstance(checkpoint_store_ref["store"], FakeStore)
    assert checkpoint_store_ref["store"].path == "/tmp/workflows.sqlite"
    assert isinstance(
        plugin_installation_store_ref["store"], FakePluginInstallationStore
    )
    assert plugin_installation_store_ref["store"].path == "/tmp/workflows.sqlite"


def test_create_repository_inmemory_backend_sets_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = DummySettings({"REPOSITORY_BACKEND": "inmemory"})
    credential_service = object()
    history_store_ref: dict[str, object] = {}
    checkpoint_store_ref: dict[str, object] = {}
    plugin_installation_store_ref: dict[str, object] = {}

    class FakeHistoryStore:
        pass

    class FakeCheckpointStore:
        pass

    class FakePluginInstallationStore:
        pass

    class FakeRepository:
        def __init__(self, *, credential_service: object) -> None:
            self.credential_service = credential_service

    monkeypatch.setattr(providers, "InMemoryRunHistoryStore", FakeHistoryStore)
    monkeypatch.setattr(
        providers, "InMemoryAgentensorCheckpointStore", FakeCheckpointStore
    )
    monkeypatch.setattr(
        providers, "InMemoryPluginInstallationStore", FakePluginInstallationStore
    )
    monkeypatch.setattr(providers, "InMemoryWorkflowRepository", FakeRepository)

    repository = providers.create_repository(
        settings,
        credential_service=credential_service,
        history_store_ref=history_store_ref,
        checkpoint_store_ref=checkpoint_store_ref,
        plugin_installation_store_ref=plugin_installation_store_ref,
    )

    assert isinstance(history_store_ref["store"], FakeHistoryStore)
    assert isinstance(checkpoint_store_ref["store"], FakeCheckpointStore)
    assert isinstance(
        plugin_installation_store_ref["store"], FakePluginInstallationStore
    )
    assert isinstance(repository, FakeRepository)
    assert repository.credential_service is credential_service


def test_create_repository_postgres_backend_sets_all_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_repository should configure postgres repositories with pool settings."""
    settings = DummySettings(
        {
            "REPOSITORY_BACKEND": "postgres",
            "POSTGRES_DSN": "postgresql://test:test@localhost/testdb",
            "POSTGRES_POOL_MIN_SIZE": 2,
            "POSTGRES_POOL_MAX_SIZE": 20,
            "POSTGRES_POOL_TIMEOUT": 15.0,
            "POSTGRES_POOL_MAX_IDLE": 500.0,
        }
    )
    credential_service = object()
    history_store_ref: dict[str, object] = {}
    checkpoint_store_ref: dict[str, object] = {}
    plugin_installation_store_ref: dict[str, object] = {}

    class FakeHistoryStore:
        def __init__(
            self,
            dsn: str,
            *,
            pool_min_size: int = 1,
            pool_max_size: int = 10,
            pool_timeout: float = 30.0,
            pool_max_idle: float = 300.0,
        ) -> None:
            self.dsn = dsn
            self.pool_min_size = pool_min_size
            self.pool_max_size = pool_max_size
            self.pool_timeout = pool_timeout
            self.pool_max_idle = pool_max_idle

    class FakeCheckpointStore:
        def __init__(
            self,
            dsn: str,
            *,
            pool_min_size: int = 1,
            pool_max_size: int = 10,
            pool_timeout: float = 30.0,
            pool_max_idle: float = 300.0,
        ) -> None:
            self.dsn = dsn
            self.pool_min_size = pool_min_size
            self.pool_max_size = pool_max_size
            self.pool_timeout = pool_timeout
            self.pool_max_idle = pool_max_idle

    class FakePluginInstallationStore:
        def __init__(
            self,
            dsn: str,
            *,
            pool_min_size: int = 1,
            pool_max_size: int = 10,
            pool_timeout: float = 30.0,
            pool_max_idle: float = 300.0,
        ) -> None:
            self.dsn = dsn
            self.pool_min_size = pool_min_size
            self.pool_max_size = pool_max_size
            self.pool_timeout = pool_timeout
            self.pool_max_idle = pool_max_idle

    class FakeRepository:
        def __init__(
            self,
            dsn: str,
            *,
            credential_service: object,
            pool_min_size: int = 1,
            pool_max_size: int = 10,
            pool_timeout: float = 30.0,
            pool_max_idle: float = 300.0,
        ) -> None:
            self.dsn = dsn
            self.credential_service = credential_service
            self.pool_min_size = pool_min_size
            self.pool_max_size = pool_max_size
            self.pool_timeout = pool_timeout
            self.pool_max_idle = pool_max_idle

    monkeypatch.setattr(providers, "PostgresRunHistoryStore", FakeHistoryStore)
    monkeypatch.setattr(
        providers, "PostgresAgentensorCheckpointStore", FakeCheckpointStore
    )
    monkeypatch.setattr(
        providers, "PostgresPluginInstallationStore", FakePluginInstallationStore
    )
    monkeypatch.setattr(providers, "PostgresWorkflowRepository", FakeRepository)

    repository = providers.create_repository(
        settings,
        credential_service=credential_service,
        history_store_ref=history_store_ref,
        checkpoint_store_ref=checkpoint_store_ref,
        plugin_installation_store_ref=plugin_installation_store_ref,
    )

    # Check repository configuration
    assert isinstance(repository, FakeRepository)
    assert repository.dsn == "postgresql://test:test@localhost/testdb"
    assert repository.credential_service is credential_service
    assert repository.pool_min_size == 2
    assert repository.pool_max_size == 20
    assert repository.pool_timeout == 15.0
    assert repository.pool_max_idle == 500.0

    # Check history store configuration
    history_store = history_store_ref["store"]
    assert isinstance(history_store, FakeHistoryStore)
    assert history_store.dsn == "postgresql://test:test@localhost/testdb"
    assert history_store.pool_min_size == 2
    assert history_store.pool_max_size == 20
    assert history_store.pool_timeout == 15.0
    assert history_store.pool_max_idle == 500.0

    # Check checkpoint store configuration
    checkpoint_store = checkpoint_store_ref["store"]
    assert isinstance(checkpoint_store, FakeCheckpointStore)
    assert checkpoint_store.dsn == "postgresql://test:test@localhost/testdb"
    assert checkpoint_store.pool_min_size == 2
    assert checkpoint_store.pool_max_size == 20
    assert checkpoint_store.pool_timeout == 15.0
    assert checkpoint_store.pool_max_idle == 500.0

    plugin_store = plugin_installation_store_ref["store"]
    assert isinstance(plugin_store, FakePluginInstallationStore)
    assert plugin_store.dsn == "postgresql://test:test@localhost/testdb"
    assert plugin_store.pool_min_size == 2
    assert plugin_store.pool_max_size == 20
    assert plugin_store.pool_timeout == 15.0
    assert plugin_store.pool_max_idle == 500.0


def test_create_vault_postgres_backend_with_all_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_vault should configure postgres vault with all pool settings."""
    settings = DummySettings(
        {
            "VAULT_BACKEND": "postgres",
            "POSTGRES_DSN": "postgresql://test:test@localhost/testdb",
            "VAULT_ENCRYPTION_KEY": "test-encryption-key-32-chars!",
            "POSTGRES_POOL_MIN_SIZE": 3,
            "POSTGRES_POOL_MAX_SIZE": 15,
        }
    )

    captured_args: dict[str, Any] = {}

    class FakeCipher:
        def __init__(self, *, key: str) -> None:
            captured_args["cipher_key"] = key

    class FakePostgresVault:
        def __init__(
            self,
            dsn: str,
            *,
            cipher: object,
            pool_min_size: int = 1,
            pool_max_size: int = 10,
        ) -> None:
            captured_args["dsn"] = dsn
            captured_args["cipher"] = cipher
            captured_args["pool_min_size"] = pool_min_size
            captured_args["pool_max_size"] = pool_max_size

    monkeypatch.setattr(providers, "AesGcmCredentialCipher", FakeCipher)
    # Mock the dynamic import
    import sys
    from unittest.mock import MagicMock

    mock_module = MagicMock()
    mock_module.PostgresCredentialVault = FakePostgresVault
    sys.modules["orcheo.vault.postgres"] = mock_module

    try:
        vault = providers.create_vault(settings)

        assert isinstance(vault, FakePostgresVault)
        assert captured_args["dsn"] == "postgresql://test:test@localhost/testdb"
        assert captured_args["cipher_key"] == "test-encryption-key-32-chars!"
        assert captured_args["pool_min_size"] == 3
        assert captured_args["pool_max_size"] == 15
    finally:
        # Clean up the mock module
        if "orcheo.vault.postgres" in sys.modules:
            del sys.modules["orcheo.vault.postgres"]


def test_create_vault_postgres_backend_with_default_pool_sizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_vault should use default pool sizes when not specified."""
    settings = DummySettings(
        {
            "VAULT_BACKEND": "postgres",
            "POSTGRES_DSN": "postgresql://test:test@localhost/testdb",
            "VAULT_ENCRYPTION_KEY": "test-encryption-key-32-chars!",
        }
    )

    captured_args: dict[str, Any] = {}

    class FakeCipher:
        def __init__(self, *, key: str) -> None:
            captured_args["cipher_key"] = key

    class FakePostgresVault:
        def __init__(
            self,
            dsn: str,
            *,
            cipher: object,
            pool_min_size: int = 1,
            pool_max_size: int = 10,
        ) -> None:
            captured_args["pool_min_size"] = pool_min_size
            captured_args["pool_max_size"] = pool_max_size

    monkeypatch.setattr(providers, "AesGcmCredentialCipher", FakeCipher)
    import sys
    from unittest.mock import MagicMock

    mock_module = MagicMock()
    mock_module.PostgresCredentialVault = FakePostgresVault
    sys.modules["orcheo.vault.postgres"] = mock_module

    try:
        providers.create_vault(settings)

        # Should use defaults of 1 and 10
        assert captured_args["pool_min_size"] == 1
        assert captured_args["pool_max_size"] == 10
    finally:
        if "orcheo.vault.postgres" in sys.modules:
            del sys.modules["orcheo.vault.postgres"]


def test_create_vault_postgres_backend_without_dsn_raises_error() -> None:
    """create_vault should raise ValueError when postgres backend lacks DSN."""
    settings = DummySettings(
        {
            "VAULT_BACKEND": "postgres",
            "VAULT_ENCRYPTION_KEY": "test-encryption-key-32-chars!",
        }
    )

    with pytest.raises(
        ValueError, match="ORCHEO_POSTGRES_DSN must be set when using the postgres"
    ):
        providers.create_vault(settings)


def test_create_vault_postgres_backend_without_encryption_key_raises_error() -> None:
    """create_vault raises ValueError when postgres backend lacks encryption key."""
    settings = DummySettings(
        {
            "VAULT_BACKEND": "postgres",
            "POSTGRES_DSN": "postgresql://test:test@localhost/testdb",
        }
    )

    with pytest.raises(
        ValueError, match="ORCHEO_VAULT_ENCRYPTION_KEY must be set when using postgres"
    ):
        providers.create_vault(settings)


def test_create_repository_sqlite_backend_without_plugin_store_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_repository tolerates omitting the plugin store ref on sqlite."""

    settings = DummySettings({"REPOSITORY_BACKEND": "sqlite"})
    credential_service = object()
    history_store_ref: dict[str, object] = {}
    checkpoint_store_ref: dict[str, object] = {}

    class FakeHistoryStore:
        def __init__(self, path: str) -> None:
            self.path = path

    class FakeCheckpointStore:
        def __init__(self, path: str) -> None:
            self.path = path

    class FakeRepository:
        def __init__(self, path: str, *, credential_service: object) -> None:
            self.path = path
            self.credential_service = credential_service

    monkeypatch.setattr(providers, "SqliteRunHistoryStore", FakeHistoryStore)
    monkeypatch.setattr(
        providers, "SqliteAgentensorCheckpointStore", FakeCheckpointStore
    )
    monkeypatch.setattr(providers, "SqliteWorkflowRepository", FakeRepository)

    repository = providers.create_repository(
        settings,
        credential_service=credential_service,
        history_store_ref=history_store_ref,
        checkpoint_store_ref=checkpoint_store_ref,
        plugin_installation_store_ref=None,
    )

    assert isinstance(repository, FakeRepository)
    assert isinstance(history_store_ref["store"], FakeHistoryStore)
    assert isinstance(checkpoint_store_ref["store"], FakeCheckpointStore)


def test_create_repository_inmemory_backend_without_plugin_store_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_repository tolerates omitting the plugin store ref on in-memory."""

    settings = DummySettings({"REPOSITORY_BACKEND": "inmemory"})
    credential_service = object()
    history_store_ref: dict[str, object] = {}
    checkpoint_store_ref: dict[str, object] = {}

    class FakeHistoryStore:
        pass

    class FakeCheckpointStore:
        pass

    class FakeRepository:
        def __init__(self, *, credential_service: object) -> None:
            self.credential_service = credential_service

    monkeypatch.setattr(providers, "InMemoryRunHistoryStore", FakeHistoryStore)
    monkeypatch.setattr(
        providers, "InMemoryAgentensorCheckpointStore", FakeCheckpointStore
    )
    monkeypatch.setattr(providers, "InMemoryWorkflowRepository", FakeRepository)

    repository = providers.create_repository(
        settings,
        credential_service=credential_service,
        history_store_ref=history_store_ref,
        checkpoint_store_ref=checkpoint_store_ref,
        plugin_installation_store_ref=None,
    )

    assert isinstance(repository, FakeRepository)
    assert isinstance(history_store_ref["store"], FakeHistoryStore)
    assert isinstance(checkpoint_store_ref["store"], FakeCheckpointStore)


def test_create_repository_postgres_backend_without_plugin_store_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_repository tolerates omitting the plugin store ref on postgres."""

    settings = DummySettings(
        {
            "REPOSITORY_BACKEND": "postgres",
            "POSTGRES_DSN": "postgresql://test:test@localhost/testdb",
        }
    )
    credential_service = object()
    history_store_ref: dict[str, object] = {}
    checkpoint_store_ref: dict[str, object] = {}

    class FakeHistoryStore:
        def __init__(
            self,
            dsn: str,
            *,
            pool_min_size: int = 1,
            pool_max_size: int = 10,
            pool_timeout: float = 30.0,
            pool_max_idle: float = 300.0,
        ) -> None:
            self.dsn = dsn

    class FakeCheckpointStore:
        def __init__(
            self,
            dsn: str,
            *,
            pool_min_size: int = 1,
            pool_max_size: int = 10,
            pool_timeout: float = 30.0,
            pool_max_idle: float = 300.0,
        ) -> None:
            self.dsn = dsn

    class FakeRepository:
        def __init__(
            self,
            dsn: str,
            *,
            credential_service: object,
            pool_min_size: int = 1,
            pool_max_size: int = 10,
            pool_timeout: float = 30.0,
            pool_max_idle: float = 300.0,
        ) -> None:
            self.dsn = dsn
            self.credential_service = credential_service

    monkeypatch.setattr(providers, "PostgresRunHistoryStore", FakeHistoryStore)
    monkeypatch.setattr(
        providers, "PostgresAgentensorCheckpointStore", FakeCheckpointStore
    )
    monkeypatch.setattr(providers, "PostgresWorkflowRepository", FakeRepository)

    repository = providers.create_repository(
        settings,
        credential_service=credential_service,
        history_store_ref=history_store_ref,
        checkpoint_store_ref=checkpoint_store_ref,
        plugin_installation_store_ref=None,
    )

    assert isinstance(repository, FakeRepository)
    assert isinstance(history_store_ref["store"], FakeHistoryStore)
    assert isinstance(checkpoint_store_ref["store"], FakeCheckpointStore)
