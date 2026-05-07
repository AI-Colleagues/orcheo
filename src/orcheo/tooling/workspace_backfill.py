"""orcheo-workspace-backfill: assign NULL workspace_id rows to a specific workspace.

Installed as the ``orcheo-workspace-backfill`` console script.

Usage:
    orcheo-workspace-backfill <workspace_slug> [--dry-run]

The PostgreSQL DSN is built from the stack .env file at
``~/.orcheo/stack/.env`` (overridable via $ORCHEO_STACK_DIR).

Tables updated (rows WHERE workspace_id IS NULL):
    workflows, workflow_versions, workflow_runs   (repository)
    credentials, credential_templates,
    governance_alerts                             (vault)
"""

from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus


TABLES = [
    "workflows",
    "workflow_versions",
    "workflow_runs",
    "credentials",
    "credential_templates",
    "governance_alerts",
]


def _stack_env_path() -> Path:
    stack_dir = os.environ.get("ORCHEO_STACK_DIR")
    if stack_dir:
        base = Path(stack_dir).expanduser()
    else:
        base = Path.home() / ".orcheo" / "stack"
    return base / ".env"


def _load_psycopg() -> tuple[Any, Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ModuleNotFoundError:
        raise SystemExit(
            "ERROR: psycopg is required. Install it with: pip install 'psycopg[binary]'"
        ) from None
    return psycopg, dict_row


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a shell-style KEY=VALUE file, ignoring comments and blank lines."""
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        result[key.strip()] = value.strip()
    return result


def _build_dsn(env: dict[str, str]) -> str:
    user = env.get("ORCHEO_POSTGRES_USER", "orcheo")
    password = env.get("ORCHEO_POSTGRES_PASSWORD", "")
    db = env.get("ORCHEO_POSTGRES_DB", "orcheo")
    port = env.get("ORCHEO_POSTGRES_LOCAL_PORT", "5432")
    return (
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}@localhost:{port}/{db}"
    )


def _resolve_dsn() -> str:
    env_path = _stack_env_path()
    if not env_path.exists():
        raise SystemExit(
            f"ERROR: stack .env not found at {env_path}.\n"
            "Run 'orcheo install' first, or set ORCHEO_STACK_DIR "
            "to point to your stack directory."
        )
    env = _parse_env_file(env_path)
    return _build_dsn(env)


def _resolve_workspace(conn: object, slug: str) -> str:
    row = conn.execute(  # type: ignore[union-attr]
        "SELECT id FROM workspaces WHERE slug = %s", (slug,)
    ).fetchone()
    if row is None:
        raise SystemExit(
            f"ERROR: workspace {slug!r} not found in the workspaces table."
        )
    return row["id"]


def _count_unscoped(conn: object, table: str) -> int:
    row = conn.execute(  # type: ignore[union-attr]
        f"SELECT COUNT(*) AS n FROM {table} WHERE workspace_id IS NULL"  # noqa: S608
    ).fetchone()
    return row["n"] if row else 0


def _assign(conn: object, table: str, workspace_id: str) -> int:
    result = conn.execute(  # type: ignore[union-attr]
        f"UPDATE {table} SET workspace_id = %s WHERE workspace_id IS NULL",  # noqa: S608
        (workspace_id,),
    )
    return result.rowcount


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orcheo-workspace-backfill",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("workspace_slug", help="Slug of the target workspace")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without writing anything",
    )
    return parser


def _print_unscoped_counts(conn: Any, psycopg: Any) -> int:
    total = 0
    for table in TABLES:
        try:
            n = _count_unscoped(conn, table)
        except psycopg.errors.UndefinedTable:
            print(f"  {table:<30} — table does not exist, skipping")
            continue
        except psycopg.errors.UndefinedColumn:
            print(f"  {table:<30} — workspace_id column missing, skipping")
            continue
        total += n
        print(f"  {table:<30} {n:>6} unscoped row(s)")
    return total


def _apply_assignments(
    conn: Any,
    psycopg: Any,
    workspace_id: str,
    workspace_slug: str,
    total: int,
) -> None:
    confirm = (
        input(f"Assign {total} row(s) to workspace {workspace_slug!r}? [y/N] ")
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


def _run(args: argparse.Namespace) -> None:
    psycopg, dict_row = _load_psycopg()
    dsn = _resolve_dsn()

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        workspace_id = _resolve_workspace(conn, args.workspace_slug)
        print(f"Target workspace : {args.workspace_slug!r} ({workspace_id})")
        print(f"Dry-run          : {args.dry_run}")
        print()

        total = _print_unscoped_counts(conn, psycopg)
        print()
        if total == 0:
            print("Nothing to do — no unscoped rows found.")
            return

        if args.dry_run:
            print(
                f"Dry-run: would assign {total} row(s). "
                "Re-run without --dry-run to apply."
            )
            return

        _apply_assignments(conn, psycopg, workspace_id, args.workspace_slug, total)

        print()
        print("Done. All unscoped rows have been assigned.")


def main() -> None:
    """Entry point for the orcheo-workspace-backfill script."""
    _run(_build_parser().parse_args())
