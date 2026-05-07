"""Reusable helpers that enforce workspace_id scoping in repository queries.

These utilities centralize the predicate so subsystems do not have to
re-implement the WHERE-clause and validation pattern in every method.

Usage:

    from orcheo.workspace.scoping import workspace_scoped_sql, ensure_workspace_id

    sql, params = workspace_scoped_sql(
        "SELECT * FROM workflows",
        workspace_id="abc-123",
    )
    # -> ("SELECT * FROM workflows WHERE workspace_id = ?", ("abc-123",))

The :class:`WorkspaceScopeError` is raised when a caller forgets to pass a
workspace id to a function that requires one. It exists so the test suite (and
optional runtime assertions) can spot missing-workspace violations explicitly.
"""

from __future__ import annotations
from typing import Any
from uuid import UUID


__all__ = [
    "WorkspaceScopeError",
    "ensure_workspace_id",
    "workspace_scoped_sql",
    "coerce_workspace_id",
]


class WorkspaceScopeError(ValueError):
    """Raised when a workspace-scoped query/operation is missing workspace context."""


def coerce_workspace_id(value: UUID | str | None) -> str:
    """Normalize a workspace id to its canonical string form.

    Accepts ``UUID``, ``str``, or ``None`` (which raises). The string form is
    used uniformly across SQLite/Postgres parameter binding for workspace ids.
    """
    if value is None:
        msg = "workspace_id must not be None"
        raise WorkspaceScopeError(msg)
    if isinstance(value, UUID):
        return str(value)
    text = str(value).strip()
    if not text:
        msg = "workspace_id must not be empty"
        raise WorkspaceScopeError(msg)
    return text


def ensure_workspace_id(
    value: UUID | str | None, *, label: str = "workspace_id"
) -> str:
    """Validate that *value* is a usable workspace id and return its canonical form.

    Args:
        value: The candidate workspace identifier.
        label: Used in error messages so callers see a useful name.
    """
    try:
        return coerce_workspace_id(value)
    except WorkspaceScopeError as exc:
        msg = f"{label} is required for workspace-scoped operations"
        raise WorkspaceScopeError(msg) from exc


def workspace_scoped_sql(
    base_query: str,
    *,
    workspace_id: UUID | str,
    column: str = "workspace_id",
    extra_params: tuple[Any, ...] = (),
    placeholder: str = "?",
) -> tuple[str, tuple[Any, ...]]:
    """Append a workspace predicate to *base_query* and return the bound params.

    The helper appends ``WHERE`` if the original query has none, otherwise
    ``AND``. Use it for simple SELECT/UPDATE/DELETE statements where the
    workspace predicate goes at the end.

    Args:
        base_query: The SQL fragment to wrap. Must not already include a
            workspace predicate.
        workspace_id: The active workspace id (UUID or str).
        column: Column name on the target table. Defaults to ``workspace_id``.
        extra_params: Additional bound parameters that follow the workspace id.
        placeholder: Parameter placeholder dialect (``?`` for SQLite,
            ``%s`` for psycopg).
    """
    tid = ensure_workspace_id(workspace_id)
    upper = base_query.upper()
    has_where = " WHERE " in upper
    connector = " AND " if has_where else " WHERE "
    predicate = f"{connector}{column} = {placeholder}"
    return base_query + predicate, (tid, *extra_params)
