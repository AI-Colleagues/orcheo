"""Tests for the tenant_scoped helper utilities."""

from __future__ import annotations
from uuid import uuid4
import pytest
from orcheo.tenancy.scoping import (
    TenantScopeError,
    coerce_tenant_id,
    ensure_tenant_id,
    tenant_scoped_sql,
)


def test_coerce_tenant_id_accepts_uuid() -> None:
    tenant = uuid4()
    assert coerce_tenant_id(tenant) == str(tenant)


def test_coerce_tenant_id_accepts_string() -> None:
    assert coerce_tenant_id("acme") == "acme"


def test_coerce_tenant_id_strips_whitespace() -> None:
    assert coerce_tenant_id("  acme  ") == "acme"


def test_coerce_tenant_id_rejects_none() -> None:
    with pytest.raises(TenantScopeError):
        coerce_tenant_id(None)


def test_coerce_tenant_id_rejects_empty_string() -> None:
    with pytest.raises(TenantScopeError):
        coerce_tenant_id("   ")


def test_ensure_tenant_id_passes_through_label() -> None:
    with pytest.raises(TenantScopeError, match="workflow_tenant"):
        ensure_tenant_id(None, label="workflow_tenant")


def test_tenant_scoped_sql_appends_where_clause() -> None:
    sql, params = tenant_scoped_sql("SELECT * FROM workflows", tenant_id="acme-uuid")
    assert sql == "SELECT * FROM workflows WHERE tenant_id = ?"
    assert params == ("acme-uuid",)


def test_tenant_scoped_sql_appends_and_when_where_exists() -> None:
    sql, params = tenant_scoped_sql(
        "SELECT * FROM workflows WHERE name = ?",
        tenant_id="acme-uuid",
        extra_params=("foo",),
    )
    # The base query already had a WHERE clause; the helper appends AND for tenant.
    assert sql.endswith("AND tenant_id = ?")
    assert params[0] == "acme-uuid"
    assert params[1] == "foo"


def test_tenant_scoped_sql_uses_postgres_placeholder() -> None:
    sql, params = tenant_scoped_sql(
        "SELECT * FROM workflows",
        tenant_id="acme",
        placeholder="%s",
    )
    assert sql.endswith("WHERE tenant_id = %s")
    assert params == ("acme",)


def test_tenant_scoped_sql_uses_custom_column() -> None:
    sql, params = tenant_scoped_sql(
        "SELECT * FROM tokens",
        tenant_id="acme",
        column="org_id",
    )
    assert sql.endswith("WHERE org_id = ?")
    assert params == ("acme",)


def test_tenant_scoped_sql_rejects_missing_tenant() -> None:
    with pytest.raises(TenantScopeError):
        tenant_scoped_sql("SELECT * FROM workflows", tenant_id="")


def test_tenant_scoped_sql_handles_uuid_tenant() -> None:
    tenant = uuid4()
    sql, params = tenant_scoped_sql("SELECT * FROM workflows", tenant_id=tenant)
    assert params == (str(tenant),)
    assert sql.endswith("WHERE tenant_id = ?")
