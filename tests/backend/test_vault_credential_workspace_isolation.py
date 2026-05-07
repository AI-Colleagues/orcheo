"""Cross-workspace isolation tests for the credential vault."""

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


def test_inmemory_list_all_credentials_filters_by_workspace() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(name="cred-a", workspace_id="workspace-a", **_COMMON_KWARGS)
    vault.create_credential(name="cred-b", workspace_id="workspace-b", **_COMMON_KWARGS)

    results_a = vault.list_all_credentials(workspace_id="workspace-a")
    results_b = vault.list_all_credentials(workspace_id="workspace-b")

    assert len(results_a) == 1
    assert results_a[0].name == "cred-a"
    assert len(results_b) == 1
    assert results_b[0].name == "cred-b"


def test_inmemory_unscoped_credential_visible_to_all_workspaces() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(name="shared", workspace_id=None, **_COMMON_KWARGS)
    vault.create_credential(name="cred-a", workspace_id="workspace-a", **_COMMON_KWARGS)

    results_a = vault.list_all_credentials(workspace_id="workspace-a")
    results_b = vault.list_all_credentials(workspace_id="workspace-b")

    names_a = {c.name for c in results_a}
    names_b = {c.name for c in results_b}
    assert "shared" in names_a
    assert "cred-a" in names_a
    assert "shared" in names_b
    assert "cred-a" not in names_b


def test_inmemory_create_credential_records_workspace_id() -> None:
    vault = InMemoryCredentialVault()
    cred = vault.create_credential(
        name="cred-x", workspace_id="workspace-x", **_COMMON_KWARGS
    )
    assert cred.workspace_id == "workspace-x"


def test_inmemory_name_uniqueness_scoped_per_workspace() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(name="dup", workspace_id="workspace-a", **_COMMON_KWARGS)
    vault.create_credential(name="dup", workspace_id="workspace-b", **_COMMON_KWARGS)

    with pytest.raises(DuplicateCredentialNameError):
        vault.create_credential(
            name="dup", workspace_id="workspace-a", **_COMMON_KWARGS
        )


def test_inmemory_no_workspace_filter_returns_all() -> None:
    vault = InMemoryCredentialVault()
    vault.create_credential(name="cred-a", workspace_id="workspace-a", **_COMMON_KWARGS)
    vault.create_credential(name="cred-b", workspace_id="workspace-b", **_COMMON_KWARGS)

    all_creds = vault.list_all_credentials()
    assert len(all_creds) == 2


# ---------------------------------------------------------------------------
# SQLite (FileCredentialVault) vault
# ---------------------------------------------------------------------------


def test_sqlite_list_all_credentials_filters_by_workspace(tmp_path: Path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.db")
    vault.create_credential(name="cred-a", workspace_id="workspace-a", **_COMMON_KWARGS)
    vault.create_credential(name="cred-b", workspace_id="workspace-b", **_COMMON_KWARGS)

    results_a = vault.list_all_credentials(workspace_id="workspace-a")
    results_b = vault.list_all_credentials(workspace_id="workspace-b")

    assert len(results_a) == 1
    assert results_a[0].name == "cred-a"
    assert len(results_b) == 1
    assert results_b[0].name == "cred-b"


def test_sqlite_unscoped_credential_visible_to_all_workspaces(tmp_path: Path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.db")
    vault.create_credential(name="shared", workspace_id=None, **_COMMON_KWARGS)
    vault.create_credential(name="cred-a", workspace_id="workspace-a", **_COMMON_KWARGS)

    results_a = vault.list_all_credentials(workspace_id="workspace-a")
    results_b = vault.list_all_credentials(workspace_id="workspace-b")

    names_a = {c.name for c in results_a}
    names_b = {c.name for c in results_b}
    assert "shared" in names_a
    assert "cred-a" in names_a
    assert "shared" in names_b
    assert "cred-a" not in names_b


def test_sqlite_create_credential_records_workspace_id(tmp_path: Path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.db")
    cred = vault.create_credential(
        name="cred-x", workspace_id="workspace-x", **_COMMON_KWARGS
    )
    assert cred.workspace_id == "workspace-x"

    reloaded = vault.list_all_credentials(workspace_id="workspace-x")
    assert len(reloaded) == 1
    assert reloaded[0].workspace_id == "workspace-x"


def test_sqlite_name_uniqueness_scoped_per_workspace(tmp_path: Path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.db")
    vault.create_credential(name="dup", workspace_id="workspace-a", **_COMMON_KWARGS)
    vault.create_credential(name="dup", workspace_id="workspace-b", **_COMMON_KWARGS)

    with pytest.raises(DuplicateCredentialNameError):
        vault.create_credential(
            name="dup", workspace_id="workspace-a", **_COMMON_KWARGS
        )


def test_sqlite_no_workspace_filter_returns_all(tmp_path: Path) -> None:
    vault = FileCredentialVault(tmp_path / "vault.db")
    vault.create_credential(name="cred-a", workspace_id="workspace-a", **_COMMON_KWARGS)
    vault.create_credential(name="cred-b", workspace_id="workspace-b", **_COMMON_KWARGS)

    all_creds = vault.list_all_credentials()
    assert len(all_creds) == 2
