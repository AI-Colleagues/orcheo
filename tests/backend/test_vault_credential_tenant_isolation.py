"""Cross-tenant isolation tests for the credential vault."""

from __future__ import annotations
from pathlib import Path
import pytest
from orcheo.vault import DuplicateCredentialNameError
from orcheo.vault.file import FileCredentialVault
from orcheo.vault.in_memory import InMemoryCredentialVault


_COMMON_KWARGS = dict(
    provider="slack",
    scopes=["chat:write"],
    secret="s3cr3t",
    actor="user",
)


# ---------------------------------------------------------------------------
# InMemory vault
# ---------------------------------------------------------------------------


def test_inmemory_list_all_credentials_filters_by_tenant() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(name="cred-a", tenant_id="tenant-a", **_COMMON_KWARGS)
    vault.create_credential(name="cred-b", tenant_id="tenant-b", **_COMMON_KWARGS)

    results_a = vault.list_all_credentials(tenant_id="tenant-a")
    results_b = vault.list_all_credentials(tenant_id="tenant-b")

    assert len(results_a) == 1
    assert results_a[0].name == "cred-a"
    assert len(results_b) == 1
    assert results_b[0].name == "cred-b"


def test_inmemory_unscoped_credential_visible_to_all_tenants() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(name="shared", tenant_id=None, **_COMMON_KWARGS)
    vault.create_credential(name="cred-a", tenant_id="tenant-a", **_COMMON_KWARGS)

    results_a = vault.list_all_credentials(tenant_id="tenant-a")
    results_b = vault.list_all_credentials(tenant_id="tenant-b")

    names_a = {c.name for c in results_a}
    names_b = {c.name for c in results_b}
    assert "shared" in names_a
    assert "cred-a" in names_a
    assert "shared" in names_b
    assert "cred-a" not in names_b


def test_inmemory_create_credential_records_tenant_id() -> None:
    vault = InMemoryCredentialVault()
    cred = vault.create_credential(
        name="cred-x", tenant_id="tenant-x", **_COMMON_KWARGS
    )
    assert cred.tenant_id == "tenant-x"


def test_inmemory_name_uniqueness_scoped_per_tenant() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(name="dup", tenant_id="tenant-a", **_COMMON_KWARGS)
    vault.create_credential(name="dup", tenant_id="tenant-b", **_COMMON_KWARGS)

    with pytest.raises(DuplicateCredentialNameError):
        vault.create_credential(name="dup", tenant_id="tenant-a", **_COMMON_KWARGS)


def test_inmemory_no_tenant_filter_returns_all() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(name="cred-a", tenant_id="tenant-a", **_COMMON_KWARGS)
    vault.create_credential(name="cred-b", tenant_id="tenant-b", **_COMMON_KWARGS)

    all_creds = vault.list_all_credentials()
    assert len(all_creds) == 2


# ---------------------------------------------------------------------------
# SQLite (FileCredentialVault) vault
# ---------------------------------------------------------------------------


def test_sqlite_list_all_credentials_filters_by_tenant(tmp_path: Path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.db")
    vault.create_credential(name="cred-a", tenant_id="tenant-a", **_COMMON_KWARGS)
    vault.create_credential(name="cred-b", tenant_id="tenant-b", **_COMMON_KWARGS)

    results_a = vault.list_all_credentials(tenant_id="tenant-a")
    results_b = vault.list_all_credentials(tenant_id="tenant-b")

    assert len(results_a) == 1
    assert results_a[0].name == "cred-a"
    assert len(results_b) == 1
    assert results_b[0].name == "cred-b"


def test_sqlite_unscoped_credential_visible_to_all_tenants(tmp_path: Path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.db")
    vault.create_credential(name="shared", tenant_id=None, **_COMMON_KWARGS)
    vault.create_credential(name="cred-a", tenant_id="tenant-a", **_COMMON_KWARGS)

    results_a = vault.list_all_credentials(tenant_id="tenant-a")
    results_b = vault.list_all_credentials(tenant_id="tenant-b")

    names_a = {c.name for c in results_a}
    names_b = {c.name for c in results_b}
    assert "shared" in names_a
    assert "cred-a" in names_a
    assert "shared" in names_b
    assert "cred-a" not in names_b


def test_sqlite_create_credential_records_tenant_id(tmp_path: Path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.db")
    cred = vault.create_credential(
        name="cred-x", tenant_id="tenant-x", **_COMMON_KWARGS
    )
    assert cred.tenant_id == "tenant-x"

    reloaded = vault.list_all_credentials(tenant_id="tenant-x")
    assert len(reloaded) == 1
    assert reloaded[0].tenant_id == "tenant-x"


def test_sqlite_name_uniqueness_scoped_per_tenant(tmp_path: Path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.db")
    vault.create_credential(name="dup", tenant_id="tenant-a", **_COMMON_KWARGS)
    vault.create_credential(name="dup", tenant_id="tenant-b", **_COMMON_KWARGS)

    with pytest.raises(DuplicateCredentialNameError):
        vault.create_credential(name="dup", tenant_id="tenant-a", **_COMMON_KWARGS)


def test_sqlite_no_tenant_filter_returns_all(tmp_path: Path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.db")
    vault.create_credential(name="cred-a", tenant_id="tenant-a", **_COMMON_KWARGS)
    vault.create_credential(name="cred-b", tenant_id="tenant-b", **_COMMON_KWARGS)

    all_creds = vault.list_all_credentials()
    assert len(all_creds) == 2
