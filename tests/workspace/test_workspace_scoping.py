"""Tests for the workspace_scoped helper utilities."""

from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.workspace.scoping import (
    WorkspaceScopeError,
    coerce_workspace_id,
    ensure_workspace_id,
    workspace_scoped_sql,
)


def test_coerce_workspace_id_accepts_uuid() -> None:
    workspace = uuid4()
    assert coerce_workspace_id(workspace) == str(workspace)


def test_coerce_workspace_id_accepts_string() -> None:
    assert coerce_workspace_id("acme") == "acme"


def test_coerce_workspace_id_strips_whitespace() -> None:
    assert coerce_workspace_id("  acme  ") == "acme"


def test_coerce_workspace_id_rejects_none() -> None:
    with pytest.raises(WorkspaceScopeError):
        coerce_workspace_id(None)


def test_coerce_workspace_id_rejects_empty_string() -> None:
    with pytest.raises(WorkspaceScopeError):
        coerce_workspace_id("   ")


def test_ensure_workspace_id_passes_through_label() -> None:
    with pytest.raises(WorkspaceScopeError, match="workflow_workspace"):
        ensure_workspace_id(None, label="workflow_workspace")


def test_workspace_scoped_sql_appends_where_clause() -> None:
    sql, params = workspace_scoped_sql(
        "SELECT * FROM workflows", workspace_id="acme-uuid"
    )
    assert sql == "SELECT * FROM workflows WHERE workspace_id = ?"
    assert params == ("acme-uuid",)


def test_workspace_scoped_sql_appends_and_when_where_exists() -> None:
    sql, params = workspace_scoped_sql(
        "SELECT * FROM workflows WHERE name = ?",
        workspace_id="acme-uuid",
        extra_params=("foo",),
    )
    # The base query already had a WHERE clause; the helper appends AND for workspace.
    assert sql.endswith("AND workspace_id = ?")
    assert params[0] == "acme-uuid"
    assert params[1] == "foo"


def test_workspace_scoped_sql_uses_postgres_placeholder() -> None:
    sql, params = workspace_scoped_sql(
        "SELECT * FROM workflows",
        workspace_id="acme",
        placeholder="%s",
    )
    assert sql.endswith("WHERE workspace_id = %s")
    assert params == ("acme",)


def test_workspace_scoped_sql_uses_custom_column() -> None:
    sql, params = workspace_scoped_sql(
        "SELECT * FROM tokens",
        workspace_id="acme",
        column="org_id",
    )
    assert sql.endswith("WHERE org_id = ?")
    assert params == ("acme",)


def test_workspace_scoped_sql_rejects_missing_workspace() -> None:
    with pytest.raises(WorkspaceScopeError):
        workspace_scoped_sql("SELECT * FROM workflows", workspace_id="")


def test_workspace_scoped_sql_handles_uuid_workspace() -> None:
    workspace = uuid4()
    sql, params = workspace_scoped_sql(
        "SELECT * FROM workflows", workspace_id=workspace
    )
    assert params == (str(workspace),)
    assert sql.endswith("WHERE workspace_id = ?")
