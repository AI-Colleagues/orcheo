"""Focused coverage for Hosted Apps process wiring and maintenance edges."""

from __future__ import annotations

import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from orcheo.hosted_apps import (
    AppDeployment,
    AppManifest,
    AppManifestBinding,
    AppSession,
    DeploymentStatus,
    InMemoryHostedAppsRepository,
)
from orcheo_backend.app.hosted_apps import auth_store, runtime_store, store
from orcheo_backend.app.hosted_apps import cleanup
from orcheo_backend.app.hosted_apps.cleanup import reconcile_filesystem
from orcheo_backend.app.hosted_apps.internal import _resolve_app_session
from orcheo_backend.app.routers import apps as apps_router
from orcheo.hosted_apps.setup_validation import validate_hosted_apps_setup
from orcheo_backend.app.schemas.apps import (
    AppBindingRequest,
    AppCollectionRequest,
    AppPublishRequest,
)
from orcheo.workspace import WorkspaceMembershipError
from orcheo.models.base import _utcnow


def test_process_wiring_selects_and_closes_postgres_adapters(monkeypatch) -> None:
    """The production adapter branches remain testable without a database server."""

    class FakePostgresRepository:
        def __init__(self, dsn: str) -> None:
            self.dsn = dsn

        def get_runtime_generation(self):
            return SimpleNamespace(enabled=False)

    class FakeAuthService:
        def __init__(self, dsn: str) -> None:
            self.dsn = dsn
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeRuntimeService(FakeAuthService):
        pass

    auth_store.reset_app_auth_service()
    runtime_store.reset_app_runtime_service()
    monkeypatch.setattr(
        auth_store, "PostgresHostedAppsRepository", FakePostgresRepository
    )
    monkeypatch.setattr(auth_store, "PostgresAppAuthService", FakeAuthService)
    monkeypatch.setattr(
        runtime_store, "PostgresHostedAppsRepository", FakePostgresRepository
    )
    monkeypatch.setattr(runtime_store, "PostgresAppRuntimeService", FakeRuntimeService)
    repository = FakePostgresRepository("postgresql://test")
    auth_service = auth_store.get_app_auth_service(repository)
    runtime_service = runtime_store.get_app_runtime_service(repository)
    assert auth_service.dsn == "postgresql://test"
    assert runtime_service.dsn == "postgresql://test"
    auth_store.reset_app_auth_service()
    runtime_store.reset_app_runtime_service()
    assert auth_service.closed is True
    assert runtime_service.closed is True


def test_postgres_json_helpers_decode_string_rows() -> None:
    """Lightweight row adapters decode JSON strings consistently."""
    from orcheo.hosted_apps.postgres_runtime import _json_value
    from orcheo.hosted_apps.postgres_store import _json_payload

    assert _json_value('{"answer": 1}') == {"answer": 1}
    assert _json_value({"answer": 1}) == {"answer": 1}
    assert _json_payload("[1, 2]") == [1, 2]
    assert _json_payload([1, 2]) == [1, 2]


def test_repository_wiring_requires_dsn_and_closes_replaced_adapter(
    monkeypatch,
) -> None:
    """Implicit production persistence is fail-closed and replacement is closed."""
    store.reset_hosted_apps_repository()
    monkeypatch.delenv("ORCHEO_POSTGRES_DSN", raising=False)
    with pytest.raises(ValueError, match="POSTGRES_DSN"):
        store.get_hosted_apps_repository()

    class FakeRepository:
        def __init__(self, dsn: str) -> None:
            self.dsn = dsn
            self.closed = False

        def get_runtime_generation(self):
            return SimpleNamespace(enabled=False)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(store, "PostgresHostedAppsRepository", FakeRepository)
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://test")
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "false")
    repository = store.get_hosted_apps_repository()
    assert repository.dsn == "postgresql://test"
    replacement = InMemoryHostedAppsRepository()
    store.set_hosted_apps_repository(replacement)
    assert repository.closed is True
    store.reset_hosted_apps_repository()
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_AUTO_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "true")
    monkeypatch.delenv("ORCHEO_APPS_BASE_DOMAIN", raising=False)
    store.get_hosted_apps_repository()
    store.reset_hosted_apps_repository()
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "filesystem")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", "/tmp/bundles")
    monkeypatch.setenv("ORCHEO_DEPLOYMENT_MODE", "local")
    monkeypatch.setattr(
        FakeRepository,
        "get_runtime_generation",
        lambda self: SimpleNamespace(enabled=True),
    )
    store.get_hosted_apps_repository()
    store.reset_hosted_apps_repository()


def test_bundle_store_wiring_migrates_legacy_filesystem_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installed PostgreSQL backend imports and then serves legacy objects."""
    migrated = []

    class FakeBundleStore:
        def __init__(self, dsn: str) -> None:
            self.dsn = dsn
            self.closed = False

        def close(self) -> None:
            self.closed = True

    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", str(tmp_path))
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://test")
    monkeypatch.setattr(store, "PostgresBundleStore", FakeBundleStore)
    monkeypatch.setattr(
        store,
        "migrate_filesystem_bundles",
        lambda root, target: migrated.append((root, target)) or 0,
    )
    store.reset_app_bundle_store()

    bundle_store = store.get_app_bundle_store()

    assert bundle_store.dsn == "postgresql://test"
    assert migrated == [(tmp_path, bundle_store)]
    store.reset_app_bundle_store()
    assert bundle_store.closed is True


def test_bundle_store_wiring_postgres_requires_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PostgreSQL bundle storage refuses to start without a configured DSN."""
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "postgres")
    monkeypatch.delenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", raising=False)
    monkeypatch.delenv("ORCHEO_POSTGRES_DSN", raising=False)
    store.reset_app_bundle_store()

    with pytest.raises(ValueError, match="POSTGRES_DSN"):
        store.get_app_bundle_store()

    store.reset_app_bundle_store()


def test_bundle_store_wiring_postgres_skips_migration_without_legacy_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No legacy filesystem migration runs when no legacy root is configured."""
    migrated = []

    class FakeBundleStore:
        def __init__(self, dsn: str) -> None:
            self.dsn = dsn
            self.closed = False

        def close(self) -> None:
            self.closed = True

    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "postgres")
    monkeypatch.delenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", raising=False)
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://test")
    monkeypatch.setattr(store, "PostgresBundleStore", FakeBundleStore)
    monkeypatch.setattr(
        store,
        "migrate_filesystem_bundles",
        lambda root, target: migrated.append((root, target)) or 0,
    )
    store.reset_app_bundle_store()

    bundle_store = store.get_app_bundle_store()

    assert bundle_store.dsn == "postgresql://test"
    assert migrated == []
    store.reset_app_bundle_store()


def test_bundle_store_wiring_rejects_unsupported_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend without a bundled adapter fails closed instead of hanging."""
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "s3")
    monkeypatch.delenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", raising=False)
    monkeypatch.delenv("ORCHEO_POSTGRES_DSN", raising=False)
    store.reset_app_bundle_store()

    with pytest.raises(ValueError, match="external upload adapter"):
        store.get_app_bundle_store()

    store.reset_app_bundle_store()


def test_bundle_store_wiring_initializes_once_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent first reads share one store instead of leaking a pool."""
    created: list[object] = []

    class FakeBundleStore:
        def __init__(self, dsn: str) -> None:
            self.dsn = dsn
            self.closed = False
            created.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setenv("ORCHEO_HOSTED_APPS_ENABLED", "true")
    monkeypatch.setenv("ORCHEO_APPS_BASE_DOMAIN", "apps.test")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", str(tmp_path))
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", "postgresql://test")
    monkeypatch.setattr(store, "PostgresBundleStore", FakeBundleStore)
    monkeypatch.setattr(store, "migrate_filesystem_bundles", lambda *_args: 0)
    store.reset_app_bundle_store()

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            stores = list(
                executor.map(lambda _: store.get_app_bundle_store(), range(8))
            )

        assert len(created) == 1
        assert all(candidate is created[0] for candidate in stores)
    finally:
        store.reset_app_bundle_store()

    assert getattr(created[0], "closed") is True


def test_cleanup_rejects_broad_roots_and_removes_files(tmp_path: Path) -> None:
    """Cleanup handles both staging files and partial directories safely."""
    with pytest.raises(ValueError, match="too broad"):
        reconcile_filesystem(Path("/"), retention_seconds=0)
    root = tmp_path / "bundles"
    staging = root / "staging"
    partial = root / "partial"
    staging.mkdir(parents=True)
    partial.mkdir(parents=True)
    old_file = staging / "old.zip"
    old_file.write_bytes(b"x")
    old_dir = partial / "old"
    old_dir.mkdir()
    os.utime(old_file, (1, 1))
    os.utime(old_dir, (1, 1))
    assert reconcile_filesystem(root, retention_seconds=60) == 2
    assert not old_file.exists()
    assert not old_dir.exists()


def test_cleanup_handles_races_and_once_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disappearing prefix is harmless and the one-shot CLI returns."""
    root = tmp_path / "bundles"
    staging = root / "staging"
    staging.mkdir(parents=True)

    class Gone:
        def stat(self):
            raise FileNotFoundError

    monkeypatch.setattr(
        Path, "iterdir", lambda self: [Gone()] if self == staging else []
    )
    assert reconcile_filesystem(root, retention_seconds=0) == 0
    monkeypatch.setattr(cleanup, "reconcile_filesystem", lambda *_a, **_k: 0)
    monkeypatch.setenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", str(root))
    monkeypatch.setenv("ORCHEO_HOSTED_APPS_CLEANUP_INTERVAL_SECONDS", "1")
    monkeypatch.setattr(sys, "argv", ["cleanup", "--once"])
    cleanup.main()


def test_hosted_apps_session_rechecks_scope_and_membership(monkeypatch) -> None:
    """Session introspection revokes cookies after scope or membership changes."""
    from orcheo_backend.app.hosted_apps import internal

    workspace_id = uuid4()
    app_id = uuid4()
    session = AppSession(
        workspace_id=workspace_id,
        app_id=app_id,
        secret_hash="a" * 64,
        app_host="app.apps.test",
        user_id="member",
        runtime_generation=1,
        expires_at=_utcnow() + timedelta(hours=1),
        idle_expires_at=_utcnow() + timedelta(minutes=30),
    )

    class FakeAuth:
        def __init__(self) -> None:
            self.revoked: list[str] = []

        def introspect(self, *_args, **_kwargs):
            return session

        def revoke(self, secret: str) -> None:
            self.revoked.append(secret)

    auth = FakeAuth()
    monkeypatch.setattr(internal, "get_app_auth_service", lambda _repo: auth)
    monkeypatch.setattr(
        internal,
        "get_workspace_repository",
        lambda: SimpleNamespace(
            get_membership=lambda *_: (_ for _ in ()).throw(
                WorkspaceMembershipError("gone")
            )
        ),
    )
    descriptor = {
        "workspace_id": str(workspace_id),
        "app_id": str(app_id),
        "generation": 1,
    }
    with pytest.raises(Exception, match="membership"):
        asyncio.run(
            _resolve_app_session(
                "secret",
                host="app.apps.test",
                descriptor=descriptor,
                repository=object(),
            )
        )
    assert auth.revoked == ["secret"]
    with pytest.raises(Exception, match="outside"):
        asyncio.run(
            _resolve_app_session(
                "secret",
                host="app.apps.test",
                descriptor={**descriptor, "app_id": str(uuid4())},
                repository=object(),
            )
        )
    assert auth.revoked == ["secret", "secret"]
    assert (
        asyncio.run(
            _resolve_app_session(
                None, host="app.apps.test", descriptor=descriptor, repository=object()
            )
        )
        is None
    )
    monkeypatch.setattr(
        internal,
        "get_workspace_repository",
        lambda: SimpleNamespace(get_membership=lambda *_: object()),
    )
    assert (
        asyncio.run(
            _resolve_app_session(
                "secret",
                host="app.apps.test",
                descriptor=descriptor,
                repository=object(),
            )
        )
        == session
    )


def test_app_schema_validators_reject_unsafe_policies() -> None:
    """Route schemas enforce bounded capability policies before repository calls."""
    base = {
        "name": "lookup",
        "workflow_id": uuid4(),
        "workflow_version_id": uuid4(),
        "access_mode": "anonymous",
    }
    with pytest.raises(ValueError, match="limits"):
        AppBindingRequest.model_validate({**base, "limits": {"max_concurrency": 0}})
    with pytest.raises(ValueError, match="projection"):
        AppBindingRequest.model_validate({**base, "output_projection": {"bad": []}})
    assert (
        AppBindingRequest.model_validate(
            {**base, "output_projection": {}}
        ).output_projection
        == {}
    )
    with pytest.raises(ValueError, match="User-scoped"):
        AppCollectionRequest(
            name="private",
            scope="user",
            read_access="anonymous",
            write_access="authenticated",
            max_document_bytes=100,
            max_records=10,
        )


def test_router_binding_resolution_rejects_archived_and_missing_workflows() -> None:
    """Router helpers translate workflow scope and repository errors consistently."""
    workspace_id = uuid4()
    workflow_id = uuid4()
    version_id = uuid4()

    class FakeWorkflows:
        async def get_workflow(self, *_args, **_kwargs):
            return SimpleNamespace(id=workflow_id, is_archived=True)

        async def get_version(self, *_args, **_kwargs):
            return SimpleNamespace(
                id=version_id,
                workflow_id=workflow_id,
                workspace_id=str(workspace_id),
                runnable_config={},
                compute_checksum=lambda: "graph",
            )

        async def resolve_workflow_ref(self, *_args, **_kwargs):
            raise ValueError("missing workflow")

    request = AppBindingRequest(
        name="lookup",
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        access_mode="anonymous",
    )
    with pytest.raises(ValueError, match="not executable"):
        asyncio.run(
            apps_router._build_binding(
                request,
                app_id=uuid4(),
                workspace_id=workspace_id,
                workflows=FakeWorkflows(),
            )
        )
    manifest = AppManifest(
        bindings={
            "lookup": AppManifestBinding(
                workflow="missing", version=1, access_mode="anonymous"
            )
        }
    )
    with pytest.raises(ValueError, match="could not resolve"):
        asyncio.run(
            apps_router._resolve_manifest_bindings(
                manifest,
                app_id=uuid4(),
                workspace_id=workspace_id,
                workflows=FakeWorkflows(),
            )
        )


def test_router_publish_maps_manifest_resolution_conflict() -> None:
    """Publishing a manifest with unresolved workflow evidence returns a conflict."""
    workspace_id = uuid4()
    repository = InMemoryHostedAppsRepository()
    from orcheo.hosted_apps import HostedApp

    app = HostedApp(workspace_id=workspace_id, name="Manifest", created_by="admin")
    repository.create_app_with_alias(app, "manifest-conflict")
    manifest = AppManifest(
        bindings={
            "lookup": AppManifestBinding(
                workflow="missing", version=1, access_mode="anonymous"
            )
        }
    )
    deployment = AppDeployment(
        workspace_id=workspace_id,
        app_id=app.id,
        status=DeploymentStatus.READY,
        app_manifest=manifest,
        created_by="admin",
    )
    repository.add_deployment(deployment)

    class MissingWorkflows:
        async def resolve_workflow_ref(self, *_args, **_kwargs):
            raise ValueError("missing")

    with pytest.raises(Exception) as error:
        asyncio.run(
            apps_router.publish_app(
                app.id,
                deployment.id,
                AppPublishRequest(
                    acknowledged_permission_revision=app.permission_revision
                ),
                repository,
                MissingWorkflows(),
                SimpleNamespace(workspace_id=workspace_id),
                SimpleNamespace(subject="admin"),
                None,
            )
        )
    assert getattr(error.value, "status_code", None) == 409


def test_setup_validation_rejects_disabled_invalid_proxy_missing_tls_and_s3() -> None:
    """Preflight fails closed for disabled, malformed, and incomplete topologies."""
    base = {
        "ORCHEO_HOSTED_APPS_ENABLED": "true",
        "ORCHEO_APPS_BASE_DOMAIN": "apps.test",
        "ORCHEO_APP_BUNDLE_BACKEND": "filesystem",
        "ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT": "/tmp/bundles",
        "ORCHEO_APP_GATEWAY_SECRET": "s" * 32,
        "ORCHEO_POSTGRES_DSN": "postgresql://test",
        "ORCHEO_HOSTED_APPS_VALIDATION_QUEUE": "queue",
    }
    with pytest.raises(ValueError, match="explicitly enabled"):
        validate_hosted_apps_setup({**base, "ORCHEO_HOSTED_APPS_ENABLED": "false"})
    with pytest.raises(ValueError):
        validate_hosted_apps_setup(
            {
                **base,
                "ORCHEO_APP_TRUSTED_PROXY_CIDRS": "invalid",
                "ORCHEO_APP_TRUSTED_PROXY_HOPS": "1",
            },
            check_dns=False,
        )
    with pytest.raises(ValueError, match="readable"):
        validate_hosted_apps_setup(
            {
                **base,
                "ORCHEO_APP_TLS_METHOD": "provided",
                "ORCHEO_APP_TLS_CERT_FILE": "/missing/cert",
                "ORCHEO_APP_TLS_KEY_FILE": "/missing/key",
            },
            check_dns=False,
        )
    s3 = {
        **base,
        "ORCHEO_APP_BUNDLE_BACKEND": "s3",
        "ORCHEO_APP_S3_ENDPOINT_URL": "https://s3.test",
    }
    with pytest.raises(ValueError, match="S3"):
        validate_hosted_apps_setup(s3, check_dns=False)
    complete_s3 = {
        **s3,
        "ORCHEO_APP_S3_REGION": "us-east-1",
        "ORCHEO_APP_S3_BUCKET": "bundles",
        "ORCHEO_APP_S3_ACCESS_KEY_ID": "access",
        "ORCHEO_APP_S3_SECRET_ACCESS_KEY": "secret",
    }
    assert (
        validate_hosted_apps_setup(complete_s3, check_dns=False)[1]
        == "bundle_backend=s3"
    )
