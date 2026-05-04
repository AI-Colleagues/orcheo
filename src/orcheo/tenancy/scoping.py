"""Reusable helpers that enforce tenant_id scoping in repository queries.

These utilities centralize the predicate so subsystems do not have to
re-implement the WHERE-clause and validation pattern in every method.

Usage:

    from orcheo.tenancy.scoping import tenant_scoped_sql, ensure_tenant_id

    sql, params = tenant_scoped_sql(
        "SELECT * FROM workflows",
        tenant_id="abc-123",
    )
    # -> ("SELECT * FROM workflows WHERE tenant_id = ?", ("abc-123",))

The :class:`TenantScopeError` is raised when a caller forgets to pass a
tenant id to a function that requires one. It exists so the test suite (and
optional runtime assertions) can spot missing-tenant violations explicitly.
"""

from __future__ import annotations
from typing import Any
from uuid import UUID


__all__ = [
    "TenantScopeError",
    "ensure_tenant_id",
    "tenant_scoped_sql",
    "coerce_tenant_id",
]


class TenantScopeError(ValueError):
    """Raised when a tenant-scoped query/operation is missing tenant context."""


def coerce_tenant_id(value: UUID | str | None) -> str:
    """Normalize a tenant id to its canonical string form.

    Accepts ``UUID``, ``str``, or ``None`` (which raises). The string form is
    used uniformly across SQLite/Postgres parameter binding for tenant ids.
    """
    if value is None:
        msg = "tenant_id must not be None"
        raise TenantScopeError(msg)
    if isinstance(value, UUID):
        return str(value)
    text = str(value).strip()
    if not text:
        msg = "tenant_id must not be empty"
        raise TenantScopeError(msg)
    return text


def ensure_tenant_id(value: UUID | str | None, *, label: str = "tenant_id") -> str:
    """Validate that *value* is a usable tenant id and return its canonical form.

    Args:
        value: The candidate tenant identifier.
        label: Used in error messages so callers see a useful name.
    """
    try:
        return coerce_tenant_id(value)
    except TenantScopeError as exc:
        msg = f"{label} is required for tenant-scoped operations"
        raise TenantScopeError(msg) from exc


def tenant_scoped_sql(
    base_query: str,
    *,
    tenant_id: UUID | str,
    column: str = "tenant_id",
    extra_params: tuple[Any, ...] = (),
    placeholder: str = "?",
) -> tuple[str, tuple[Any, ...]]:
    """Append a tenant predicate to *base_query* and return the bound params.

    The helper appends ``WHERE`` if the original query has none, otherwise
    ``AND``. Use it for simple SELECT/UPDATE/DELETE statements where the
    tenant predicate goes at the end.

    Args:
        base_query: The SQL fragment to wrap. Must not already include a
            tenant predicate.
        tenant_id: The active tenant id (UUID or str).
        column: Column name on the target table. Defaults to ``tenant_id``.
        extra_params: Additional bound parameters that follow the tenant id.
        placeholder: Parameter placeholder dialect (``?`` for SQLite,
            ``%s`` for psycopg).
    """
    tid = ensure_tenant_id(tenant_id)
    upper = base_query.upper()
    has_where = " WHERE " in upper
    connector = " AND " if has_where else " WHERE "
    predicate = f"{connector}{column} = {placeholder}"
    return base_query + predicate, (tid, *extra_params)
