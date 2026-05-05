"""Unit tests for workspace domain models."""

from __future__ import annotations
import pytest
from orcheo.workspace import (
    Role,
    Workspace,
    WorkspaceContext,
    WorkspaceQuotas,
    WorkspaceStatus,
    normalize_slug,
)


def test_role_includes_uses_rank_hierarchy() -> None:
    assert Role.OWNER.includes(Role.VIEWER)
    assert Role.OWNER.includes(Role.OWNER)
    assert Role.ADMIN.includes(Role.EDITOR)
    assert not Role.EDITOR.includes(Role.ADMIN)
    assert not Role.VIEWER.includes(Role.EDITOR)


def test_normalize_slug_lowercases_and_validates() -> None:
    assert normalize_slug("Acme") == "acme"
    assert normalize_slug("acme-prod_1") == "acme-prod_1"
    with pytest.raises(ValueError, match="empty"):
        normalize_slug("   ")
    with pytest.raises(ValueError, match="alphanumeric"):
        normalize_slug("acme inc")


def test_workspace_validates_slug_and_name() -> None:
    workspace = Workspace(slug="Acme", name="Acme Inc")
    assert workspace.slug == "acme"
    assert workspace.status is WorkspaceStatus.ACTIVE
    assert workspace.quotas == WorkspaceQuotas()
    with pytest.raises(ValueError):
        Workspace(slug="", name="x")
    with pytest.raises(ValueError):
        Workspace(slug="ok", name="   ")


def test_workspace_quotas_defaults_are_positive() -> None:
    quotas = WorkspaceQuotas()
    assert quotas.max_workflows >= 1
    assert quotas.max_concurrent_runs >= 1
    assert quotas.max_credentials >= 1
    assert quotas.max_storage_rows >= 1


def test_workspace_context_round_trips_through_headers() -> None:
    workspace = Workspace(slug="Acme", name="Acme Inc")
    ctx = WorkspaceContext(
        workspace_id=workspace.id,
        workspace_slug=workspace.slug,
        user_id="alice",
        role=Role.ADMIN,
        quotas=workspace.quotas,
    )
    headers = ctx.to_headers()
    assert headers["x-orcheo-workspace-slug"] == "acme"
    assert headers["x-orcheo-role"] == "admin"
    rebuilt = WorkspaceContext.from_headers({**headers, "quotas": workspace.quotas})
    assert rebuilt.workspace_id == workspace.id
    assert rebuilt.role is Role.ADMIN


def test_workspace_context_from_headers_requires_keys() -> None:
    with pytest.raises(ValueError, match="Missing workspace header"):
        WorkspaceContext.from_headers({})


def test_workspace_context_has_role() -> None:
    workspace = Workspace(slug="acme", name="Acme")
    ctx = WorkspaceContext(
        workspace_id=workspace.id,
        workspace_slug=workspace.slug,
        user_id="u",
        role=Role.EDITOR,
    )
    assert ctx.has_role(Role.VIEWER)
    assert ctx.has_role(Role.EDITOR)
    assert not ctx.has_role(Role.ADMIN)
