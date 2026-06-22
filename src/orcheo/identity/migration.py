"""One-time identity backfill: re-key memberships from Auth0 ``sub`` to user id.

At cutover, every workspace membership is still keyed by the Auth0 ``sub``. This
module creates a :class:`User` per distinct captured ``membership.email`` and
re-keys each ``sub``-keyed membership onto the matching internal user id using
the ``reassign_membership`` primitive. The backfill is **idempotent**: re-running
it makes no further changes once memberships already point at internal ids.

Because the cutover has no dual-run window, this is the primary migration path,
not a straggler cleanup. ``report_migration_coverage`` provides a non-mutating
readiness check for the same data.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from orcheo.identity.models import User
from orcheo.identity.repository import IdentityRepository
from orcheo.workspace.errors import WorkspaceMembershipError
from orcheo.workspace.models import Role, WorkspaceMembership
from orcheo.workspace.repository import WorkspaceRepository
from orcheo.workspace.resolver import WorkspaceResolver


__all__ = [
    "CoverageReport",
    "MigrationReport",
    "backfill_identities",
    "report_migration_coverage",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MigrationReport:
    """Outcome of a backfill run."""

    total_memberships: int
    users_created: int
    memberships_rekeyed: int
    memberships_already_internal: int
    collisions_resolved: int
    memberships_without_email: int

    @property
    def migrated(self) -> int:
        """Memberships now keyed by an internal user id after this run."""
        return self.memberships_rekeyed + self.memberships_already_internal

    @property
    def fully_covered(self) -> bool:
        """True when every email-bearing membership is keyed by an internal id."""
        return self.memberships_without_email == 0 and self.migrated == (
            self.total_memberships - self.memberships_without_email
        )


@dataclass(frozen=True)
class CoverageReport:
    """Non-mutating view of migration readiness."""

    total_memberships: int
    keyed_by_internal_id: int
    remaining_to_migrate: int
    memberships_without_email: int

    @property
    def fully_covered(self) -> bool:
        """True when no email-bearing membership remains to migrate."""
        return self.remaining_to_migrate == 0 and self.memberships_without_email == 0


def _all_memberships(
    workspace_repo: WorkspaceRepository,
) -> list[WorkspaceMembership]:
    memberships: list[WorkspaceMembership] = []
    for workspace in workspace_repo.list_workspaces(include_inactive=True):
        memberships.extend(workspace_repo.list_memberships_for_workspace(workspace.id))
    return memberships


def _ensure_users_for_emails(
    memberships: list[WorkspaceMembership],
    identity_repo: IdentityRepository,
) -> tuple[dict[str, User], int]:
    """Find-or-create a user per distinct membership email; return map + created."""
    email_to_user: dict[str, User] = {}
    created = 0
    for membership in memberships:
        if not membership.email:
            continue
        key = membership.email.strip().lower()
        if key in email_to_user:
            continue
        existing = identity_repo.get_user_by_email(key)
        if existing is None:
            existing = identity_repo.create_user(User(email=key))
            created += 1
        email_to_user[key] = existing
    return email_to_user, created


def backfill_identities(
    *,
    identity_repo: IdentityRepository,
    workspace_repo: WorkspaceRepository,
    resolver: WorkspaceResolver | None = None,
) -> MigrationReport:
    """Create users from membership emails and re-key memberships idempotently."""
    memberships = _all_memberships(workspace_repo)
    email_to_user, users_created = _ensure_users_for_emails(memberships, identity_repo)

    rekeyed = 0
    already_internal = 0
    collisions = 0
    without_email = 0
    touched_ids: set[str] = set()

    for membership in memberships:
        if not membership.email:
            without_email += 1
            continue
        target_id = str(email_to_user[membership.email.strip().lower()].id)
        if membership.user_id == target_id:
            already_internal += 1
            continue
        try:
            workspace_repo.reassign_membership(
                membership.workspace_id, membership.user_id, target_id
            )
            rekeyed += 1
        except WorkspaceMembershipError:
            _resolve_collision(workspace_repo, membership, target_id)
            collisions += 1
        touched_ids.add(membership.user_id)
        touched_ids.add(target_id)

    if resolver is not None:
        for user_id in touched_ids:
            resolver.invalidate(user_id)

    report = MigrationReport(
        total_memberships=len(memberships),
        users_created=users_created,
        memberships_rekeyed=rekeyed,
        memberships_already_internal=already_internal,
        collisions_resolved=collisions,
        memberships_without_email=without_email,
    )
    logger.info("Identity backfill complete: %s", report)
    return report


def _resolve_collision(
    workspace_repo: WorkspaceRepository,
    source: WorkspaceMembership,
    target_id: str,
) -> None:
    """Resolve a ``(workspace_id, target_id)`` collision: keep target, highest role.

    The pre-existing internal-id row is kept; the duplicate ``sub``-keyed row is
    dropped. If the dropped row carried a higher role, the kept row is promoted.
    """
    existing = workspace_repo.get_membership(source.workspace_id, target_id)
    if Role(source.role).rank > Role(existing.role).rank:
        workspace_repo.update_membership_role(
            source.workspace_id, target_id, Role(source.role)
        )
    workspace_repo.remove_membership(source.workspace_id, source.user_id)


def report_migration_coverage(
    *,
    identity_repo: IdentityRepository,
    workspace_repo: WorkspaceRepository,
) -> CoverageReport:
    """Report migration coverage without mutating any data (cutover readiness)."""
    memberships = _all_memberships(workspace_repo)
    keyed_internal = 0
    remaining = 0
    without_email = 0

    for membership in memberships:
        if not membership.email:
            without_email += 1
            continue
        user = identity_repo.get_user_by_email(membership.email.strip().lower())
        if user is not None and membership.user_id == str(user.id):
            keyed_internal += 1
        else:
            remaining += 1

    return CoverageReport(
        total_memberships=len(memberships),
        keyed_by_internal_id=keyed_internal,
        remaining_to_migrate=remaining,
        memberships_without_email=without_email,
    )
