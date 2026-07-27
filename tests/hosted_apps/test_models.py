"""Tests for Hosted Apps framework-independent validation and models."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4
import pytest
from pydantic import ValidationError
from orcheo.hosted_apps import (
    AliasValidationError,
    AppAlias,
    AppBinding,
    AppCollection,
    HostedApp,
    ReservedAliasError,
    normalize_alias,
    normalize_logical_name,
)
from orcheo.hosted_apps.postgres_schema import POSTGRES_HOSTED_APPS_SCHEMA
from orcheo.models.base import _utcnow


def test_alias_normalizes_to_lowercase() -> None:
    """Alias normalization is stable before persistence enforces uniqueness."""
    assert normalize_alias(" Research-Portal ") == "research-portal"
    assert AppAlias(alias="RESEARCH-PORTAL").alias == "research-portal"


@pytest.mark.parametrize("value", ["ab", "a-", "-a", "with_space", "éclair"])
def test_alias_rejects_invalid_dns_labels(value: str) -> None:
    """Aliases must remain exact wildcard-domain labels."""
    with pytest.raises(AliasValidationError):
        normalize_alias(value)


def test_alias_rejects_reserved_platform_name() -> None:
    """Platform endpoints cannot be claimed as app aliases."""
    with pytest.raises(ReservedAliasError):
        normalize_alias("api")


def test_logical_names_are_normalized_but_not_slugified() -> None:
    """Binding and collection names have deterministic logical identities."""
    assert normalize_logical_name(" Generate_Report ") == "generate_report"
    with pytest.raises(ValueError, match="Names must start"):
        normalize_logical_name("1-report")


def test_app_overlay_precedence_is_safe_for_display() -> None:
    """Suspension and archive always mask the older publication state."""
    app = HostedApp(workspace_id=uuid4(), name="Portal", created_by="user")
    assert app.derived_state == "draft"
    app.is_archived = True
    assert app.derived_state == "archived"
    app.suspended_at = _utcnow()
    assert app.derived_state == "suspended"


def test_binding_and_collection_keep_stable_ids() -> None:
    """Deletion is a tombstone instead of name-based record resurrection."""
    app_id = uuid4()
    workspace_id = uuid4()
    binding = AppBinding(
        app_id=app_id,
        workspace_id=workspace_id,
        name="REPORT",
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        workflow_execution_sha256="a" * 64,
        access_mode="anonymous",
    )
    collection = AppCollection(
        app_id=app_id,
        workspace_id=workspace_id,
        name="Preferences",
        scope="user",
        read_access="authenticated",
        write_access="authenticated",
        max_document_bytes=1024,
        max_records=10,
    )
    assert binding.name == "report"
    assert collection.name == "preferences"
    assert collection.deleted_at is None
    collection.deleted_at = _utcnow() + timedelta(seconds=1)
    assert collection.id != uuid4()


def test_app_name_must_not_be_blank() -> None:
    """App identity cannot be created without a display name."""
    with pytest.raises(ValidationError, match="App name must not be empty"):
        HostedApp(workspace_id=uuid4(), name="  ", created_by="user")


def test_postgres_schema_covers_cross_plane_ownership_constraints() -> None:
    """The idempotent DDL includes login state and ready-release enforcement."""
    assert "hosted_app_login_transactions" in POSTGRES_HOSTED_APPS_SCHEMA
    assert "fk_hosted_apps_active_release" in POSTGRES_HOSTED_APPS_SCHEMA
    assert "validate_hosted_app_ready_release" in POSTGRES_HOSTED_APPS_SCHEMA
