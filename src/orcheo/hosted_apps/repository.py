"""Hosted Apps repository protocol and transactional in-memory reference store."""

from __future__ import annotations
from collections.abc import Callable
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Protocol
from uuid import UUID
from orcheo.hosted_apps.errors import (
    AliasConflictError,
    AliasTombstonedError,
    HostedAppsDisabledError,
)
from orcheo.hosted_apps.models import (
    AliasLifecycle,
    AppAlias,
    AppBinding,
    AppCollection,
    AppDeployment,
    AppRelease,
    DeploymentStatus,
    HostedApp,
    ModerationBlock,
    PlatformAuditEvent,
    PublicationState,
    RuntimeGeneration,
    normalize_alias,
)
from orcheo.models.base import _utcnow


__all__ = ["HostedAppsRepository", "InMemoryHostedAppsRepository"]


class HostedAppsRepository(Protocol):
    """Persistence protocol that keeps every operation tenant- and app-scoped."""

    def create_app_with_alias(self, app: HostedApp, alias: str) -> AppAlias:
        """Atomically persist an app and reserve its initial alias."""

    def get_app(self, workspace_id: UUID, app_id: UUID) -> HostedApp:
        """Return an app only when it belongs to the requested workspace."""

    def list_apps(self, workspace_id: UUID) -> list[HostedApp]:
        """List apps belonging to the requested workspace only."""

    def list_apps_page(
        self,
        workspace_id: UUID,
        *,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[list[tuple[HostedApp, AppAlias]], bool]:
        """List one cursor page with aliases and whether more rows exist."""

    def get_active_deployment_id(self, workspace_id: UUID, app_id: UUID) -> UUID | None:
        """Return the deployment selected by the app's active release."""

    def update_app(
        self,
        app: HostedApp,
        *,
        actor: str | None = None,
        action: str = "app.update",
    ) -> HostedApp:
        """Persist a changed app after verifying its workspace/app ownership."""

    def reserve_alias(
        self, app: HostedApp, alias: str, *, actor: str | None = None
    ) -> AppAlias:
        """Atomically assign a new alias and tombstone the previous one."""

    def get_alias(self, workspace_id: UUID, app_id: UUID) -> AppAlias:
        """Return the active alias for one workspace-owned app."""

    def add_deployment(self, deployment: AppDeployment) -> AppDeployment:
        """Persist an app-owned deployment candidate."""

    def list_deployments(self, workspace_id: UUID, app_id: UUID) -> list[AppDeployment]:
        """List deployment candidates within one authoritative app scope."""

    def save_binding(self, binding: AppBinding, *, actor: str) -> AppBinding:
        """Create or replace one draft binding under a composite app scope."""

    def list_bindings(self, workspace_id: UUID, app_id: UUID) -> list[AppBinding]:
        """List live draft bindings for one app."""

    def delete_binding(
        self, workspace_id: UUID, app_id: UUID, binding_id: UUID, *, actor: str
    ) -> None:
        """Tombstone a draft binding."""

    def invalidate_bindings_for_workflow(
        self, workspace_id: UUID, workflow_id: UUID, *, actor: str
    ) -> int:
        """Invalidate app permission reviews that reference a changed workflow."""

    def save_collection(
        self, collection: AppCollection, *, actor: str
    ) -> AppCollection:
        """Create or replace one app-data collection."""

    def list_collections(self, workspace_id: UUID, app_id: UUID) -> list[AppCollection]:
        """List live collection definitions for one app."""

    def delete_collection(
        self, workspace_id: UUID, app_id: UUID, collection_id: UUID, *, actor: str
    ) -> None:
        """Tombstone an app-data collection."""

    def publish_release(self, release: AppRelease) -> HostedApp:
        """Atomically select a validated immutable release for its owning app."""

    def unpublish(
        self, workspace_id: UUID, app_id: UUID, *, actor: str | None = None
    ) -> HostedApp:
        """Stop resolving an app while retaining immutable release history."""

    def get_runtime_generation(self) -> RuntimeGeneration:
        """Return durable feature and runtime enablement state."""

    def resolve_descriptor(self, alias: str) -> dict[str, Any]:
        """Resolve one active alias to its immutable gateway descriptor."""

    def list_audit_events(
        self, workspace_id: UUID, app_id: UUID
    ) -> list[PlatformAuditEvent]:
        """List mutation audit evidence for one app."""

    def reserve_platform_alias(self, alias: str, *, actor: str) -> AppAlias:
        """Reserve a globally unavailable platform alias."""

    def create_moderation_block(
        self,
        *,
        target_kind: str,
        target_id: str,
        reason_code: str,
        reason_detail: str | None,
        actor: str,
    ) -> ModerationBlock:
        """Create a platform moderation override."""

    def lift_moderation_block(self, block_id: UUID, *, actor: str) -> ModerationBlock:
        """Lift one platform moderation override."""

    def lookup_alias_owner(self, alias: str) -> dict[str, str] | None:
        """Return platform-safe alias ownership metadata."""

    def set_runtime_enabled(self, *, enabled: bool, actor: str) -> RuntimeGeneration:
        """Change runtime availability and increment its cache generation."""

    def assert_runtime_enabled(self, expected_generation: int | None = None) -> None:
        """Fail closed when runtime delivery is unavailable or stale."""


class InMemoryHostedAppsRepository:
    """Synchronized lifecycle reference used by unit tests and embedded dev flows.

    It makes the expected transactional invariants executable. Production must use a
    Postgres implementation with equivalent locking and composite foreign keys.
    """

    def __init__(
        self,
        *,
        alias_tombstone_days: int = 30,
        audit_hook: Callable[[PlatformAuditEvent], None] | None = None,
    ) -> None:
        """Initialize empty records and a fail-closed runtime generation."""
        self._lock = RLock()
        self._alias_tombstone_days = alias_tombstone_days
        self._apps: dict[UUID, HostedApp] = {}
        self._aliases: dict[str, AppAlias] = {}
        self._deployments: dict[UUID, AppDeployment] = {}
        self._releases: dict[UUID, AppRelease] = {}
        self._bindings: dict[UUID, AppBinding] = {}
        self._collections: dict[UUID, AppCollection] = {}
        self._moderation_blocks: dict[UUID, ModerationBlock] = {}
        self._audit_events: list[PlatformAuditEvent] = []
        self._audit_hook = audit_hook
        self._runtime = RuntimeGeneration()

    def create_app_with_alias(self, app: HostedApp, alias: str) -> AppAlias:
        """Create app and initial alias under one lock, rolling back on conflict."""
        with self._lock:
            if app.id in self._apps:
                raise ValueError("Hosted app already exists.")
            reservation = self._prepare_alias(app, alias)
            self._record_audit("app.create", app.created_by, app)
            self._apps[app.id] = app.model_copy(deep=True)
            self._aliases[reservation.alias] = reservation.model_copy(deep=True)
            return reservation.model_copy(deep=True)

    def get_app(self, workspace_id: UUID, app_id: UUID) -> HostedApp:
        """Return an app only within its authoritative workspace scope."""
        with self._lock:
            app = self._apps.get(app_id)
            if app is None or app.workspace_id != workspace_id:
                raise KeyError("Hosted app was not found in this workspace.")
            return app.model_copy(deep=True)

    def list_apps(self, workspace_id: UUID) -> list[HostedApp]:
        """List workspace-owned apps with newest updates first."""
        with self._lock:
            return sorted(
                (
                    app.model_copy(deep=True)
                    for app in self._apps.values()
                    if app.workspace_id == workspace_id
                ),
                key=lambda app: (app.updated_at, str(app.id)),
                reverse=True,
            )

    def get_active_deployment_id(self, workspace_id: UUID, app_id: UUID) -> UUID | None:
        """Return the deployment selected by the app's active release."""
        with self._lock:
            app = self._ensure_app_scope(workspace_id, app_id)
            if app.active_release_id is None:
                return None
            release = self._releases.get(app.active_release_id)
            if release is None:
                raise RuntimeError("Hosted app active release is unavailable.")
            return release.deployment_id

    def list_apps_page(
        self,
        workspace_id: UUID,
        *,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[list[tuple[HostedApp, AppAlias]], bool]:
        """List one cursor page without loading the full workspace collection."""
        apps = self.list_apps(workspace_id)
        if cursor is not None:
            apps = [app for app in apps if (app.updated_at, app.id) < cursor]
        selected = apps[: limit + 1]
        page = selected[:limit]
        return (
            [(app, self.get_alias(workspace_id, app.id)) for app in page],
            len(selected) > limit,
        )

    def update_app(
        self,
        app: HostedApp,
        *,
        actor: str | None = None,
        action: str = "app.update",
    ) -> HostedApp:
        """Update only the persisted app in its original workspace scope."""
        with self._lock:
            persisted = self._ensure_existing(app)
            self._record_audit(action, actor or app.created_by, app)
            app.updated_at = _utcnow()
            stored = app.model_copy(deep=True)
            self._apps[persisted.id] = stored
            return stored.model_copy(deep=True)

    def reserve_alias(
        self, app: HostedApp, alias: str, *, actor: str | None = None
    ) -> AppAlias:
        """Replace the active alias only after the requested alias is claimable."""
        with self._lock:
            self._ensure_existing(app)
            reservation = self._prepare_alias(app, alias)
            self._record_audit("alias.reserve", actor or app.created_by, app)
            self._aliases[reservation.alias] = reservation.model_copy(deep=True)
            for current in self._aliases.values():
                if current.app_id == app.id and current.alias != reservation.alias:
                    current.app_id = None
                    current.workspace_id = None
                    current.reserved_kind = AliasLifecycle.TOMBSTONE
                    current.tombstoned_until = _utcnow() + timedelta(
                        days=self._alias_tombstone_days
                    )
                    current.updated_at = _utcnow()
            return reservation.model_copy(deep=True)

    def get_alias(self, workspace_id: UUID, app_id: UUID) -> AppAlias:
        """Return the one active platform alias within a workspace/app scope."""
        with self._lock:
            self._ensure_app_scope(workspace_id, app_id)
            for alias in self._aliases.values():
                if (
                    alias.workspace_id == workspace_id
                    and alias.app_id == app_id
                    and alias.reserved_kind is AliasLifecycle.APP
                ):
                    return alias.model_copy(deep=True)
            raise KeyError("Hosted app alias was not found.")

    def add_deployment(self, deployment: AppDeployment) -> AppDeployment:
        """Persist only a deployment that belongs to an existing app and workspace."""
        with self._lock:
            app = self._ensure_app_scope(deployment.workspace_id, deployment.app_id)
            if deployment.id in self._deployments:
                raise ValueError("Hosted app deployment already exists.")
            self._deployments[deployment.id] = deployment.model_copy(deep=True)
            if (
                deployment.status is DeploymentStatus.READY
                and deployment.app_manifest is not None
            ):
                self._record_audit(
                    "deployment.manifest.request", deployment.created_by, app
                )
                app.permission_revision += 1
                app.updated_at = _utcnow()
            return deployment.model_copy(deep=True)

    def list_deployments(self, workspace_id: UUID, app_id: UUID) -> list[AppDeployment]:
        """List app-owned deployments newest first."""
        with self._lock:
            self._ensure_app_scope(workspace_id, app_id)
            return sorted(
                (
                    deployment.model_copy(deep=True)
                    for deployment in self._deployments.values()
                    if deployment.workspace_id == workspace_id
                    and deployment.app_id == app_id
                ),
                key=lambda deployment: deployment.created_at,
                reverse=True,
            )

    def save_binding(self, binding: AppBinding, *, actor: str) -> AppBinding:
        """Create or replace one draft binding under a composite app scope."""
        with self._lock:
            app = self._ensure_app_scope(binding.workspace_id, binding.app_id)
            for current in self._bindings.values():
                if (
                    current.id != binding.id
                    and current.app_id == binding.app_id
                    and current.deleted_at is None
                    and current.name == binding.name
                ):
                    raise ValueError("A live binding already uses this name.")
            existing = self._bindings.get(binding.id)
            if existing is not None and (
                existing.workspace_id != binding.workspace_id
                or existing.app_id != binding.app_id
            ):
                raise KeyError("Hosted app binding was not found.")
            self._record_audit("capability.binding.save", actor, app)
            stored = binding.model_copy(deep=True)
            stored.updated_at = _utcnow()
            self._bindings[stored.id] = stored
            app.permission_revision += 1
            app.updated_at = stored.updated_at
            return stored.model_copy(deep=True)

    def list_bindings(self, workspace_id: UUID, app_id: UUID) -> list[AppBinding]:
        """List only live draft bindings for an authoritative app scope."""
        with self._lock:
            self._ensure_app_scope(workspace_id, app_id)
            return [
                item.model_copy(deep=True)
                for item in self._bindings.values()
                if item.workspace_id == workspace_id
                and item.app_id == app_id
                and item.deleted_at is None
            ]

    def delete_binding(
        self, workspace_id: UUID, app_id: UUID, binding_id: UUID, *, actor: str
    ) -> None:
        """Tombstone a draft binding while preserving release snapshots."""
        with self._lock:
            app = self._ensure_app_scope(workspace_id, app_id)
            binding = self._bindings.get(binding_id)
            if (
                binding is None
                or binding.workspace_id != workspace_id
                or binding.app_id != app_id
                or binding.deleted_at is not None
            ):
                raise KeyError("Hosted app binding was not found.")
            self._record_audit("capability.binding.delete", actor, app)
            binding.deleted_at = _utcnow()
            binding.updated_at = binding.deleted_at
            app.permission_revision += 1
            app.updated_at = binding.deleted_at

    def invalidate_bindings_for_workflow(
        self, workspace_id: UUID, workflow_id: UUID, *, actor: str
    ) -> int:
        """Mark draft review stale when a referenced runnable config changes."""
        with self._lock:
            app_ids = {
                binding.app_id
                for binding in self._bindings.values()
                if binding.workspace_id == workspace_id
                and binding.workflow_id == workflow_id
                and binding.deleted_at is None
            }
            for app_id in app_ids:
                app = self._ensure_app_scope(workspace_id, app_id)
                self._record_audit(
                    "capability.binding.dependency_invalidated", actor, app
                )
                app.permission_revision += 1
                app.updated_at = _utcnow()
            return len(app_ids)

    def save_collection(
        self, collection: AppCollection, *, actor: str
    ) -> AppCollection:
        """Create or replace a stable-id collection definition."""
        with self._lock:
            app = self._ensure_app_scope(collection.workspace_id, collection.app_id)
            for current in self._collections.values():
                if (
                    current.id != collection.id
                    and current.app_id == collection.app_id
                    and current.deleted_at is None
                    and current.name == collection.name
                ):
                    raise ValueError("A live collection already uses this name.")
            existing = self._collections.get(collection.id)
            if existing is not None and (
                existing.workspace_id != collection.workspace_id
                or existing.app_id != collection.app_id
            ):
                raise KeyError("Hosted app collection was not found.")
            self._record_audit("capability.collection.save", actor, app)
            stored = collection.model_copy(deep=True)
            stored.updated_at = _utcnow()
            self._collections[stored.id] = stored
            app.permission_revision += 1
            app.updated_at = stored.updated_at
            return stored.model_copy(deep=True)

    def list_collections(self, workspace_id: UUID, app_id: UUID) -> list[AppCollection]:
        """List only live collection definitions in one app scope."""
        with self._lock:
            self._ensure_app_scope(workspace_id, app_id)
            return [
                item.model_copy(deep=True)
                for item in self._collections.values()
                if item.workspace_id == workspace_id
                and item.app_id == app_id
                and item.deleted_at is None
            ]

    def delete_collection(
        self, workspace_id: UUID, app_id: UUID, collection_id: UUID, *, actor: str
    ) -> None:
        """Tombstone a stable collection id so name reuse cannot resurrect rows."""
        with self._lock:
            app = self._ensure_app_scope(workspace_id, app_id)
            collection = self._collections.get(collection_id)
            if (
                collection is None
                or collection.workspace_id != workspace_id
                or collection.app_id != app_id
                or collection.deleted_at is not None
            ):
                raise KeyError("Hosted app collection was not found.")
            self._record_audit("capability.collection.delete", actor, app)
            collection.deleted_at = _utcnow()
            collection.updated_at = collection.deleted_at
            app.permission_revision += 1
            app.updated_at = collection.deleted_at

    def publish_release(self, release: AppRelease) -> HostedApp:
        """Select a release only when deployment, app, and revision all match."""
        with self._lock:
            app = self._ensure_app_scope(release.workspace_id, release.app_id)
            if app.is_archived or app.suspended_at is not None:
                raise ValueError("Archived or suspended apps cannot be published.")
            deployment = self._deployments.get(release.deployment_id)
            if (
                deployment is None
                or deployment.app_id != app.id
                or deployment.workspace_id != app.workspace_id
                or deployment.status is not DeploymentStatus.READY
            ):
                raise ValueError(
                    "Release deployment is not a ready app-owned deployment."
                )
            if release.permission_revision != app.permission_revision:
                raise ValueError(
                    "Release must acknowledge the current permission revision."
                )
            if release.id in self._releases:
                raise ValueError("Hosted app release already exists.")
            self._record_audit("release.publish", release.created_by, app)
            self._releases[release.id] = release.model_copy(deep=True)
            app.active_release_id = release.id
            app.publication_state = PublicationState.PUBLISHED
            app.published_permission_revision = release.permission_revision
            app.published_at = _utcnow()
            app.updated_at = app.published_at
            return app.model_copy(deep=True)

    def unpublish(
        self, workspace_id: UUID, app_id: UUID, *, actor: str | None = None
    ) -> HostedApp:
        """Atomically stop delivery without deleting the last release pointer."""
        with self._lock:
            app = self._ensure_app_scope(workspace_id, app_id)
            self._record_audit("release.unpublish", actor or app.created_by, app)
            app.publication_state = PublicationState.UNPUBLISHED
            app.updated_at = _utcnow()
            return app.model_copy(deep=True)

    def list_audit_events(
        self, workspace_id: UUID, app_id: UUID
    ) -> list[PlatformAuditEvent]:
        """Return app-scoped mutation evidence without cross-workspace access."""
        with self._lock:
            self._ensure_app_scope(workspace_id, app_id)
            target_id = str(app_id)
            return [
                event.model_copy(deep=True)
                for event in self._audit_events
                if event.target_kind == "app"
                and event.target_id == target_id
                and event.metadata.get("workspace_id") == str(workspace_id)
            ]

    def get_runtime_generation(self) -> RuntimeGeneration:
        """Return the durable-like current runtime state."""
        with self._lock:
            return self._runtime.model_copy(deep=True)

    def resolve_descriptor(self, alias: str) -> dict[str, Any]:
        """Resolve an enabled published alias without accepting workspace input."""
        with self._lock:
            self.assert_runtime_enabled()
            reservation = self._aliases.get(normalize_alias(alias))
            if (
                reservation is None
                or reservation.reserved_kind is not AliasLifecycle.APP
                or reservation.app_id is None
                or reservation.workspace_id is None
            ):
                raise KeyError("Hosted app alias was not found.")
            app = self._ensure_app_scope(reservation.workspace_id, reservation.app_id)
            if app.suspended_at is not None or self._is_platform_blocked(
                reservation.alias, app
            ):
                return {
                    "alias": reservation.alias,
                    "state": "suspended",
                    "generation": self._runtime.generation,
                }
            if (
                app.is_archived
                or app.publication_state is not PublicationState.PUBLISHED
                or app.active_release_id is None
            ):
                raise KeyError("Hosted app alias is not published.")
            release = self._releases.get(app.active_release_id)
            if release is None:
                raise KeyError("Hosted app release was not found.")
            return {
                "alias": reservation.alias,
                "state": "published",
                "generation": self._runtime.generation,
                "workspace_id": str(app.workspace_id),
                "app_id": str(app.id),
                "release_id": str(release.id),
                "deployment_id": str(release.deployment_id),
                "visibility": release.visibility.value,
                "snapshot_sha256": release.snapshot_sha256,
                "capability_snapshot": release.capability_snapshot,
            }

    def reserve_platform_alias(self, alias: str, *, actor: str) -> AppAlias:
        """Reserve a global platform alias outside workspace authority."""
        with self._lock:
            normalized = normalize_alias(alias)
            if normalized in self._aliases:
                raise ValueError("Hosted app alias is already reserved.")
            reservation = AppAlias(
                alias=normalized, reserved_kind=AliasLifecycle.PLATFORM
            )
            self._record_platform_audit(
                "moderation.alias.reserve", actor, "alias", normalized
            )
            self._aliases[normalized] = reservation
            return reservation.model_copy(deep=True)

    def create_moderation_block(
        self,
        *,
        target_kind: str,
        target_id: str,
        reason_code: str,
        reason_detail: str | None,
        actor: str,
    ) -> ModerationBlock:
        """Atomically create a platform override and audit evidence."""
        if target_kind not in {"app", "alias", "workspace", "publisher"}:
            raise ValueError("Hosted Apps moderation target is invalid.")
        with self._lock:
            block = ModerationBlock(
                target_kind=target_kind,
                target_id=target_id,
                reason_code=reason_code,
                reason_detail=reason_detail,
                created_by=actor,
            )
            self._record_platform_audit(
                "moderation.block",
                actor,
                target_kind,
                target_id,
                reason_code=reason_code,
            )
            self._moderation_blocks[block.id] = block
            return block.model_copy(deep=True)

    def lift_moderation_block(self, block_id: UUID, *, actor: str) -> ModerationBlock:
        """Atomically reinstate a blocked target and record the operator."""
        with self._lock:
            block = self._moderation_blocks.get(block_id)
            if block is None:
                raise KeyError("Hosted Apps moderation block was not found.")
            if block.lifted_at is None:
                self._record_platform_audit(
                    "moderation.reinstate",
                    actor,
                    block.target_kind,
                    block.target_id,
                    reason_code=block.reason_code,
                )
                block.lifted_by = actor
                block.lifted_at = _utcnow()
            return block.model_copy(deep=True)

    def lookup_alias_owner(self, alias: str) -> dict[str, str] | None:
        """Return platform-safe alias ownership metadata."""
        with self._lock:
            reservation = self._aliases.get(normalize_alias(alias))
            if reservation is None:
                return None
            return {
                "alias": reservation.alias,
                "kind": reservation.reserved_kind.value,
                "app_id": str(reservation.app_id) if reservation.app_id else "",
                "workspace_id": (
                    str(reservation.workspace_id) if reservation.workspace_id else ""
                ),
            }

    def set_runtime_enabled(self, *, enabled: bool, actor: str) -> RuntimeGeneration:
        """Increment runtime generation on every control change to invalidate caches."""
        with self._lock:
            self._record_platform_audit(
                "runtime_generation.update",
                actor,
                "runtime",
                "global",
            )
            self._runtime.generation += 1
            self._runtime.enabled = enabled
            self._runtime.updated_by = actor
            self._runtime.updated_at = _utcnow()
            return self._runtime.model_copy(deep=True)

    def assert_runtime_enabled(self, expected_generation: int | None = None) -> None:
        """Fail closed on disable or when a cached descriptor becomes stale."""
        with self._lock:
            if not self._runtime.enabled:
                raise HostedAppsDisabledError("Hosted Apps runtime is disabled.")
            if (
                expected_generation is not None
                and expected_generation != self._runtime.generation
            ):
                raise HostedAppsDisabledError(
                    "Hosted Apps runtime generation is stale."
                )

    def _prepare_alias(self, app: HostedApp, raw_alias: str) -> AppAlias:
        """Validate an alias and build its reservation without changing state."""
        alias = normalize_alias(raw_alias)
        existing = self._aliases.get(alias)
        now = _utcnow()
        if existing is not None:
            if existing.app_id == app.id:
                return existing
            if (
                existing.reserved_kind is AliasLifecycle.TOMBSTONE
                and existing.tombstoned_until is not None
                and existing.tombstoned_until <= now
            ):
                pass
            elif existing.reserved_kind is AliasLifecycle.TOMBSTONE:
                raise AliasTombstonedError("App alias is temporarily tombstoned.")
            else:
                raise AliasConflictError("App alias is already reserved.")
        reservation = AppAlias(
            alias=alias,
            app_id=app.id,
            workspace_id=app.workspace_id,
            reserved_kind=AliasLifecycle.APP,
        )
        return reservation

    def _record_audit(self, action: str, actor: str, app: HostedApp) -> None:
        """Persist mutation evidence before state commit; propagate any failure."""
        event = PlatformAuditEvent(
            action=action,
            actor=actor,
            target_kind="app",
            target_id=str(app.id),
            metadata={"workspace_id": str(app.workspace_id)},
        )
        if self._audit_hook is not None:
            self._audit_hook(event)
        self._audit_events.append(event)

    def _record_platform_audit(
        self,
        action: str,
        actor: str,
        target_kind: str,
        target_id: str,
        *,
        reason_code: str | None = None,
    ) -> None:
        event = PlatformAuditEvent(
            action=action,
            actor=actor,
            target_kind=target_kind,
            target_id=target_id,
            reason_code=reason_code,
        )
        if self._audit_hook is not None:
            self._audit_hook(event)
        self._audit_events.append(event)

    def _is_platform_blocked(self, alias: str, app: HostedApp) -> bool:
        targets = {
            ("alias", alias),
            ("app", str(app.id)),
            ("workspace", str(app.workspace_id)),
            ("publisher", app.created_by),
        }
        return any(
            block.lifted_at is None and (block.target_kind, block.target_id) in targets
            for block in self._moderation_blocks.values()
        )

    def _ensure_existing(self, app: HostedApp) -> HostedApp:
        """Ensure the supplied app belongs to the persisted workspace/app pair."""
        return self._ensure_app_scope(app.workspace_id, app.id)

    def _ensure_app_scope(self, workspace_id: UUID, app_id: UUID) -> HostedApp:
        """Implement mandatory composite workspace/app ownership checks."""
        app = self._apps.get(app_id)
        if app is None or app.workspace_id != workspace_id:
            raise KeyError("Hosted app was not found in this workspace.")
        return app
