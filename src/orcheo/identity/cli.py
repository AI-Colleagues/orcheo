"""Management CLI for the one-time identity backfill and coverage reporting.

Usage::

    python -m orcheo.identity.cli backfill        # run the idempotent backfill
    python -m orcheo.identity.cli coverage         # non-mutating readiness report

Both build the Postgres-backed identity and workspace repositories from the same
``ORCHEO_POSTGRES_DSN`` the backend uses.
"""

from __future__ import annotations
import argparse
import logging
import sys
from orcheo.config import get_settings
from orcheo.identity.migration import (
    backfill_identities,
    report_migration_coverage,
)
from orcheo.identity.postgres_store import PostgresIdentityRepository
from orcheo.identity.repository import IdentityRepository
from orcheo.workspace.postgres_store import PostgresWorkspaceRepository
from orcheo.workspace.repository import WorkspaceRepository


logger = logging.getLogger(__name__)


def _build_repositories() -> tuple[IdentityRepository, WorkspaceRepository]:
    settings = get_settings()
    dsn = settings.get("POSTGRES_DSN")
    if not dsn:
        msg = "ORCHEO_POSTGRES_DSN must be set to run the identity migration."
        raise SystemExit(msg)
    return (
        PostgresIdentityRepository(str(dsn)),
        PostgresWorkspaceRepository(str(dsn)),
    )


def _run_backfill() -> int:
    identity_repo, workspace_repo = _build_repositories()
    report = backfill_identities(
        identity_repo=identity_repo, workspace_repo=workspace_repo
    )
    print(  # noqa: T201 - CLI output
        "Identity backfill complete:\n"
        f"  total memberships:        {report.total_memberships}\n"
        f"  users created:            {report.users_created}\n"
        f"  memberships re-keyed:      {report.memberships_rekeyed}\n"
        f"  already internal:         {report.memberships_already_internal}\n"
        f"  collisions resolved:      {report.collisions_resolved}\n"
        f"  without email (skipped):  {report.memberships_without_email}\n"
        f"  fully covered:            {report.fully_covered}"
    )
    return 0 if report.fully_covered else 1


def _run_coverage() -> int:
    identity_repo, workspace_repo = _build_repositories()
    report = report_migration_coverage(
        identity_repo=identity_repo, workspace_repo=workspace_repo
    )
    print(  # noqa: T201 - CLI output
        "Migration coverage:\n"
        f"  total memberships:        {report.total_memberships}\n"
        f"  keyed by internal id:     {report.keyed_by_internal_id}\n"
        f"  remaining to migrate:     {report.remaining_to_migrate}\n"
        f"  without email:            {report.memberships_without_email}\n"
        f"  fully covered:            {report.fully_covered}"
    )
    return 0 if report.fully_covered else 1


def main(argv: list[str] | None = None) -> int:
    """Entry point for the identity migration CLI."""
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="orcheo-identity-migrate")
    parser.add_argument(
        "command",
        choices=("backfill", "coverage"),
        help="backfill: run the idempotent migration; coverage: report only.",
    )
    args = parser.parse_args(argv)
    if args.command == "backfill":
        return _run_backfill()
    return _run_coverage()


if __name__ == "__main__":  # pragma: no cover - module CLI entry
    sys.exit(main())
