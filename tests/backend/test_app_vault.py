"""Tests covering vault creation and key management helpers."""

from __future__ import annotations
from types import SimpleNamespace

import pytest

from orcheo.models import AesGcmCredentialCipher
from orcheo_backend.app import _create_vault


def test_create_vault_rejects_inmemory_backend() -> None:
    """Non-postgres vault backends are rejected."""

    settings = SimpleNamespace(
        vault=SimpleNamespace(backend="inmemory", encryption_key=None)
    )

    with pytest.raises(ValueError, match="Vault backend must be 'postgres'\\."):
        _create_vault(settings)  # type: ignore[arg-type]


def test_create_vault_supports_postgres_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres vaults require a DSN and encryption key."""

    settings = SimpleNamespace(
        vault=SimpleNamespace(
            backend="postgres",
            encryption_key="unit-test-key",
        ),
        postgres_dsn="postgresql://example",
        postgres_pool_min_size=2,
        postgres_pool_max_size=5,
    )

    captured: dict[str, object] = {}

    class FakePostgresVault:
        def __init__(
            self,
            dsn: str,
            *,
            cipher: object,
            pool_min_size: int,
            pool_max_size: int,
        ) -> None:
            captured["dsn"] = dsn
            captured["cipher"] = cipher
            captured["pool_min_size"] = pool_min_size
            captured["pool_max_size"] = pool_max_size

    monkeypatch.setattr(
        "orcheo.vault.postgres.PostgresCredentialVault",
        FakePostgresVault,
    )

    vault = _create_vault(settings)  # type: ignore[arg-type]

    assert isinstance(vault, FakePostgresVault)
    assert captured["dsn"] == "postgresql://example"
    assert captured["pool_min_size"] == 2
    assert captured["pool_max_size"] == 5


def test_create_vault_rejects_unsupported_backend() -> None:
    """Unsupported vault backends raise a clear error message."""

    settings = SimpleNamespace(vault=SimpleNamespace(backend="file"))

    with pytest.raises(ValueError, match="Vault backend must be 'postgres'\\."):
        _create_vault(settings)  # type: ignore[arg-type]
