"""Lifecycle invariant tests for the Hosted Apps reference repository."""

from __future__ import annotations

from uuid import uuid4
import pytest
from orcheo.hosted_apps import (
    AliasTombstonedError,
    AliasConflictError,
    AppBinding,
    AppCollection,
    AppDeployment,
    AppRelease,
    AppVisibility,
    DeploymentStatus,
    HostedApp,
    HostedAppsDisabledError,
    InMemoryHostedAppsRepository,
    PublicationState,
)


def _app(workspace_id: object | None = None) -> HostedApp:
    """Build a minimal app in a specified or fresh workspace."""
    return HostedApp(
        workspace_id=workspace_id or uuid4(), name="Portal", created_by="author"
    )


def test_initial_alias_reservation_is_atomic_and_global() -> None:
    """Different workspaces cannot race into the same wildcard-host alias."""
    repository = InMemoryHostedAppsRepository()
    first = _app()
    repository.create_app_with_alias(first, "portal")
    with pytest.raises(AliasConflictError):
        repository.create_app_with_alias(_app(), "PORTAL")


def test_tenant_scope_is_mandatory_for_app_lookup() -> None:
    """An app id alone never authorizes cross-workspace reads."""
    repository = InMemoryHostedAppsRepository()
    app = _app()
    repository.create_app_with_alias(app, "scope-test")
    with pytest.raises(KeyError):
        repository.get_app(uuid4(), app.id)


def test_publish_requires_ready_deployment_current_revision_and_ownership() -> None:
    """A release cannot accidentally point at another app's or invalid bytes."""
    repository = InMemoryHostedAppsRepository()
    app = _app()
    repository.create_app_with_alias(app, "publish-test")
    deployment = AppDeployment(
        app_id=app.id,
        workspace_id=app.workspace_id,
        status=DeploymentStatus.READY,
        created_by="author",
    )
    repository.add_deployment(deployment)
    release = AppRelease(
        workspace_id=app.workspace_id,
        app_id=app.id,
        deployment_id=deployment.id,
        permission_revision=app.permission_revision,
        visibility=AppVisibility.PUBLIC,
        capability_snapshot={},
        csp_snapshot={},
        snapshot_sha256="a" * 64,
        created_by="admin",
    )
    published = repository.publish_release(release)
    assert published.publication_state is PublicationState.PUBLISHED
    assert published.active_release_id == release.id


def test_runtime_generation_fails_closed_and_invalidates_cache() -> None:
    """Disable and generation changes stop cached gateway/runtime authorization."""
    repository = InMemoryHostedAppsRepository()
    with pytest.raises(HostedAppsDisabledError):
        repository.assert_runtime_enabled()
    state = repository.set_runtime_enabled(enabled=True, actor="operator")
    repository.assert_runtime_enabled(state.generation)
    repository.set_runtime_enabled(enabled=False, actor="operator")
    with pytest.raises(HostedAppsDisabledError):
        repository.assert_runtime_enabled(state.generation)


def test_unpublish_keeps_release_pointer_for_reviewed_rollback() -> None:
    """Unpublish stops delivery without destroying immutable release history."""
    repository = InMemoryHostedAppsRepository()
    app = _app()
    repository.create_app_with_alias(app, "rollback-test")
    deployment = AppDeployment(
        app_id=app.id,
        workspace_id=app.workspace_id,
        status=DeploymentStatus.READY,
        created_by="author",
    )
    repository.add_deployment(deployment)
    release = AppRelease(
        workspace_id=app.workspace_id,
        app_id=app.id,
        deployment_id=deployment.id,
        permission_revision=app.permission_revision,
        visibility=AppVisibility.PUBLIC,
        capability_snapshot={},
        csp_snapshot={},
        snapshot_sha256="b" * 64,
        created_by="admin",
    )
    repository.publish_release(release)
    unpublished = repository.unpublish(app.workspace_id, app.id)
    assert unpublished.publication_state is PublicationState.UNPUBLISHED
    assert unpublished.active_release_id == release.id


def test_gateway_descriptor_resolves_only_enabled_active_release() -> None:
    """Host resolution copies immutable ids and observes runtime generation."""
    repository = InMemoryHostedAppsRepository()
    app = _app()
    repository.create_app_with_alias(app, "descriptor-test")
    deployment = AppDeployment(
        app_id=app.id,
        workspace_id=app.workspace_id,
        status=DeploymentStatus.READY,
        created_by="author",
    )
    repository.add_deployment(deployment)
    release = AppRelease(
        workspace_id=app.workspace_id,
        app_id=app.id,
        deployment_id=deployment.id,
        permission_revision=app.permission_revision,
        visibility=AppVisibility.PUBLIC,
        capability_snapshot={},
        csp_snapshot={},
        snapshot_sha256="c" * 64,
        created_by="admin",
    )
    repository.publish_release(release)
    generation = repository.set_runtime_enabled(enabled=True, actor="operator")
    descriptor = repository.resolve_descriptor("descriptor-test")
    assert descriptor["release_id"] == str(release.id)
    assert descriptor["deployment_id"] == str(deployment.id)
    assert descriptor["generation"] == generation.generation
    repository.unpublish(app.workspace_id, app.id)
    with pytest.raises(KeyError):
        repository.resolve_descriptor("descriptor-test")


def test_audit_failure_prevents_state_mutation() -> None:
    """Sensitive mutations do not commit when durable audit persistence fails."""

    def fail_updates(event) -> None:
        if event.action == "app.update":
            raise RuntimeError("audit store unavailable")

    repository = InMemoryHostedAppsRepository(audit_hook=fail_updates)
    app = _app()
    repository.create_app_with_alias(app, "audit-test")
    changed = repository.get_app(app.workspace_id, app.id)
    changed.name = "Changed"
    with pytest.raises(RuntimeError, match="audit store unavailable"):
        repository.update_app(changed, actor="editor")
    assert repository.get_app(app.workspace_id, app.id).name == "Portal"
    events = repository.list_audit_events(app.workspace_id, app.id)
    assert [event.action for event in events] == ["app.create"]


def test_platform_block_overrides_published_app_until_reinstated() -> None:
    repository = InMemoryHostedAppsRepository()
    app = _app()
    repository.create_app_with_alias(app, "moderated-app")
    deployment = AppDeployment(
        app_id=app.id,
        workspace_id=app.workspace_id,
        status=DeploymentStatus.READY,
        created_by="author",
    )
    repository.add_deployment(deployment)
    release = AppRelease(
        workspace_id=app.workspace_id,
        app_id=app.id,
        deployment_id=deployment.id,
        permission_revision=1,
        visibility=AppVisibility.PUBLIC,
        capability_snapshot={},
        csp_snapshot={},
        snapshot_sha256="f" * 64,
        created_by="admin",
    )
    repository.publish_release(release)
    repository.set_runtime_enabled(enabled=True, actor="operator")
    block = repository.create_moderation_block(
        target_kind="app",
        target_id=str(app.id),
        reason_code="abuse",
        reason_detail=None,
        actor="operator",
    )
    assert repository.resolve_descriptor("moderated-app")["state"] == "suspended"
    repository.lift_moderation_block(block.id, actor="operator")
    assert repository.resolve_descriptor("moderated-app")["state"] == "published"


def test_reference_repository_edge_invariants() -> None:
    """Exercise duplicate, stale, tombstone, and release-integrity guards."""
    repository = InMemoryHostedAppsRepository()
    app = _app()
    repository.create_app_with_alias(app, "repo-edge")
    with pytest.raises(ValueError, match="already exists"):
        repository.create_app_with_alias(app, "repo-edge")
    repository._apps[app.id].active_release_id = uuid4()  # noqa: SLF001
    with pytest.raises(RuntimeError, match="active release"):
        repository.get_active_deployment_id(app.workspace_id, app.id)
    repository._aliases.pop("repo-edge")  # noqa: SLF001
    with pytest.raises(KeyError):
        repository.get_alias(app.workspace_id, app.id)

    deployment = AppDeployment(
        app_id=app.id,
        workspace_id=app.workspace_id,
        status=DeploymentStatus.READY,
        created_by="author",
    )
    repository._apps[app.id].active_release_id = None  # noqa: SLF001
    repository.add_deployment(deployment)
    with pytest.raises(ValueError, match="already exists"):
        repository.add_deployment(deployment)
    with pytest.raises(ValueError, match="ready"):
        repository.publish_release(
            AppRelease(
                workspace_id=app.workspace_id,
                app_id=app.id,
                deployment_id=uuid4(),
                permission_revision=1,
                visibility=AppVisibility.PUBLIC,
                capability_snapshot={},
                csp_snapshot={},
                snapshot_sha256="a" * 64,
                created_by="author",
            )
        )
    binding = AppBinding(
        workspace_id=app.workspace_id,
        app_id=app.id,
        name="edge-binding",
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        workflow_execution_sha256="a" * 64,
        access_mode="anonymous",
    )
    repository.save_binding(binding, actor="author")
    with pytest.raises(ValueError, match="binding"):
        repository.save_binding(
            binding.model_copy(update={"id": uuid4()}), actor="author"
        )
    wrong_binding = binding.model_copy(update={"app_id": uuid4()})
    with pytest.raises(KeyError):
        repository.save_binding(wrong_binding, actor="author")
    foreign_app = _app(app.workspace_id)
    repository.create_app_with_alias(foreign_app, "foreign-edge")
    foreign_binding = binding.model_copy(
        update={"app_id": foreign_app.id, "id": uuid4(), "name": "foreign-binding"}
    )
    repository.save_binding(foreign_binding, actor="author")
    with pytest.raises(KeyError):
        repository.save_binding(
            foreign_binding.model_copy(update={"app_id": app.id}), actor="author"
        )
    assert (
        repository.invalidate_bindings_for_workflow(
            app.workspace_id, binding.workflow_id, actor="author"
        )
        == 2
    )
    wrong_collection = AppCollection(
        workspace_id=app.workspace_id,
        app_id=app.id,
        name="edge-data",
        scope="shared",
        read_access="anonymous",
        write_access="anonymous",
        max_document_bytes=100,
        max_records=10,
    )
    repository.save_collection(wrong_collection, actor="author")
    with pytest.raises(KeyError):
        repository.save_collection(
            wrong_collection.model_copy(update={"app_id": uuid4()}),
            actor="author",
        )
    foreign_collection = wrong_collection.model_copy(
        update={"app_id": foreign_app.id, "id": uuid4(), "name": "foreign-data"}
    )
    repository.save_collection(foreign_collection, actor="author")
    with pytest.raises(KeyError):
        repository.save_collection(
            foreign_collection.model_copy(update={"app_id": app.id}), actor="author"
        )
    repository._apps[app.id].is_archived = True  # noqa: SLF001
    archived_release = AppRelease(
        workspace_id=app.workspace_id,
        app_id=app.id,
        deployment_id=deployment.id,
        permission_revision=repository._apps[app.id].permission_revision,  # noqa: SLF001
        visibility=AppVisibility.PUBLIC,
        capability_snapshot={},
        csp_snapshot={},
        snapshot_sha256="d" * 64,
        created_by="author",
    )
    with pytest.raises(ValueError, match="Archived"):
        repository.publish_release(archived_release)
    repository._apps[app.id].is_archived = False  # noqa: SLF001
    duplicate_release = archived_release.model_copy(
        update={"id": uuid4(), "snapshot_sha256": "e" * 64}
    )
    repository.publish_release(duplicate_release)
    with pytest.raises(ValueError, match="already exists"):
        repository.publish_release(duplicate_release)
    with pytest.raises(ValueError, match="moderation target"):
        repository.create_moderation_block(
            target_kind="invalid",
            target_id="target",
            reason_code="bad",
            reason_detail=None,
            actor="operator",
        )
    block = repository.create_moderation_block(
        target_kind="app",
        target_id=str(app.id),
        reason_code="abuse",
        reason_detail=None,
        actor="operator",
    )
    assert repository.lift_moderation_block(block.id, actor="operator").lifted_at
    assert repository.lift_moderation_block(block.id, actor="operator").lifted_at
    with pytest.raises(KeyError):
        repository.lift_moderation_block(uuid4(), actor="operator")
    repository.set_runtime_enabled(enabled=True, actor="operator")
    with pytest.raises(Exception):
        repository.assert_runtime_enabled(expected_generation=99)
    missing_release_app = _app(app.workspace_id)
    repository.create_app_with_alias(missing_release_app, "missing-release")
    repository._apps[missing_release_app.id].active_release_id = uuid4()  # noqa: SLF001
    repository._apps[
        missing_release_app.id
    ].publication_state = PublicationState.PUBLISHED  # noqa: SLF001
    with pytest.raises(KeyError, match="release"):
        repository.resolve_descriptor("missing-release")


def test_reference_repository_alias_tombstone_and_release_guards() -> None:
    """Expired tombstones can be reclaimed while live ones remain reserved."""
    repository = InMemoryHostedAppsRepository()
    app = _app()
    repository.create_app_with_alias(app, "tombstone-edge")
    repository.reserve_alias(app, "tombstone-new", actor="author")
    assert repository._prepare_alias(app, "tombstone-new").app_id == app.id  # noqa: SLF001
    with pytest.raises(AliasTombstonedError):
        repository.reserve_alias(
            app.model_copy(update={"created_by": "other"}),
            "tombstone-edge",
            actor="other",
        )
    alias = repository._aliases["tombstone-edge"]  # noqa: SLF001
    alias.reserved_kind = type(alias.reserved_kind).TOMBSTONE
    alias.app_id = None
    alias.workspace_id = None
    alias.tombstoned_until = alias.updated_at
    assert (
        repository.reserve_alias(app, "tombstone-edge", actor="author").app_id == app.id
    )


def test_platform_alias_audit_failure_is_propagated() -> None:
    """Platform reservations do not hide durable audit failures."""

    def fail_audit(_event) -> None:
        raise RuntimeError("audit unavailable")

    repository = InMemoryHostedAppsRepository(audit_hook=fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        repository.reserve_platform_alias("blocked", actor="operator")
