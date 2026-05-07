#!/usr/bin/env python3
"""Assign workspace IDs to previously unscoped rows.

This script updates every table that carries a `workspace_id` column.

Usage:
    python deploy/assign_workspace.py <workspace_slug> [--dsn DSN] [--dry-run]

Arguments:
    workspace_slug  Slug of the target workspace (from the workspaces table).
    --dsn           PostgreSQL DSN.  Falls back to ORCHEO_POSTGRES_DSN env var.
    --dry-run       Print row counts without committing any changes.

Tables updated (rows WHERE workspace_id IS NULL):
    workflows, workflow_versions, workflow_runs   (repository)
    credentials, credential_templates,
    governance_alerts                             (vault)
"""

from __future__ import annotations
import argparse
import os
import sys
import psycopg
from psycopg.rows import dict_row


TABLES = [
    "workflows",
    "workflow_versions",
    "workflow_runs",
    "credentials",
    "credential_templates",
    "governance_alerts",
]


def _count_unscoped(conn: psycopg.Connection, table: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE workspace_id IS NULL"  # noqa: S608
    ).fetchone()
    return row["n"] if row else 0


def _assign(conn: psycopg.Connection, table: str, workspace_id: str) -> int:
    result = conn.execute(
        f"UPDATE {table} SET workspace_id = %s WHERE workspace_id IS NULL",  # noqa: S608
        (workspace_id,),
    )
    return result.rowcount


def _resolve_workspace(conn: psycopg.Connection, slug: str) -> str:
    """Return the workspace UUID for slug, or raise if not found."""
    row = conn.execute(
        "SELECT id FROM workspaces WHERE slug = %s",
        (slug,),
    ).fetchone()
    if row is None:
        raise SystemExit(
            f"ERROR: workspace {slug!r} not found in the workspaces table."
        )
    return row["id"]


def _build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("workspace_slug", help="Slug of the target workspace")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("ORCHEO_POSTGRES_DSN"),
        help="PostgreSQL DSN (default: $ORCHEO_POSTGRES_DSN)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report counts without updating"
    )
    return parser


def _print_unscoped_counts(conn: psycopg.Connection) -> int:
    """Print unscoped row counts for all configured tables."""
    total_unscoped = 0
    for table in TABLES:
        try:
            n = _count_unscoped(conn, table)
        except psycopg.errors.UndefinedTable:
            print(f"  {table:<30} — table does not exist, skipping")
            continue
        except psycopg.errors.UndefinedColumn:
            print(f"  {table:<30} — workspace_id column missing, skipping")
            continue
        total_unscoped += n
        print(f"  {table:<30} {n:>6} unscoped row(s)")
    return total_unscoped


def _apply_assignments(
    conn: psycopg.Connection,
    workspace_slug: str,
    workspace_id: str,
    total_unscoped: int,
) -> None:
    """Confirm and assign all unscoped rows to the target workspace."""
    confirm = (
        input(f"Assign {total_unscoped} row(s) to workspace {workspace_slug!r}? [y/N] ")
        .strip()
        .lower()
    )
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    print()
    with conn.transaction():
        for table in TABLES:
            try:
                updated = _assign(conn, table, workspace_id)
            except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
                continue
            if updated:
                print(f"  {table:<30} {updated:>6} row(s) updated")


def main() -> None:
    """Run the workspace backfill command-line tool."""
    args = _build_parser().parse_args()
    if not args.dsn:
        raise SystemExit("ERROR: provide --dsn or set ORCHEO_POSTGRES_DSN.")

    with psycopg.connect(args.dsn, row_factory=dict_row) as conn:
        workspace_id = _resolve_workspace(conn, args.workspace_slug)
        print(f"Target workspace : {args.workspace_slug!r} ({workspace_id})")
        print(f"Dry-run          : {args.dry_run}")
        print()

        total_unscoped = _print_unscoped_counts(conn)

        print()
        if total_unscoped == 0:
            print("Nothing to do — no unscoped rows found.")
            return

        if args.dry_run:
            print(f"Dry-run: would assign {total_unscoped} row(s).")
            print("Re-run without --dry-run to apply.")
            return

        _apply_assignments(conn, args.workspace_slug, workspace_id, total_unscoped)

        print()
        print("Done. All unscoped rows have been assigned.")


if __name__ == "__main__":
    main()
