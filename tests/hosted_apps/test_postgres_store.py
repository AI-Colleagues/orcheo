"""PostgreSQL persistence checks for Hosted Apps metadata."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from psycopg import connect
from psycopg.types.json import Jsonb

from orcheo.hosted_apps import (
    AppBinding,
    AppCollection,
    AppDeployment,
    AppRelease,
    AppVisibility,
    DeploymentStatus,
    HostedApp,
    AppRuntimeLimitError,
    PostgresAppRuntimeService,
    PostgresAppAuthService,
    PostgresHostedAppsRepository,
    PublicationState,
    pkce_challenge,
)
from orcheo.workspace import PostgresWorkspaceRepository, Workspace


def test_postgres_repository_survives_fresh_process_adapter() -> None:
    """A newly constructed repository can read a complete published app."""
    if os.getenv("ORCHEO_TEST_POSTGRES_PERSISTENCE") != "1":
        pytest.skip("Postgres persistence integration checks are not enabled.")

    dsn = os.getenv("ORCHEO_POSTGRES_DSN")
    if dsn is None:
        pytest.fail("ORCHEO_POSTGRES_DSN is required for this integration check.")

    workspace_repository = PostgresWorkspaceRepository(dsn)
    workspace = Workspace(
        slug=f"hosted-app-{uuid4().hex[:12]}",
        name="Hosted Apps persistence test",
    )
    workspace_repository.create_workspace(workspace)
    app = HostedApp(
        workspace_id=workspace.id,
        name="Persistent portal",
        created_by="integration-test",
    )
    deployment = AppDeployment(
        workspace_id=workspace.id,
        app_id=app.id,
        status=DeploymentStatus.READY,
        archive_sha256="a" * 64,
        manifest_sha256="b" * 64,
        created_by="integration-test",
    )
    binding = AppBinding(
        workspace_id=workspace.id,
        app_id=app.id,
        name="submit",
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        workflow_execution_sha256="c" * 64,
        access_mode="anonymous",
    )
    collection = AppCollection(
        workspace_id=workspace.id,
        app_id=app.id,
        name="submissions",
        scope="shared",
        read_access="authenticated",
        write_access="anonymous",
        max_document_bytes=4096,
        max_records=100,
    )
    release = AppRelease(
        workspace_id=workspace.id,
        app_id=app.id,
        deployment_id=deployment.id,
        permission_revision=3,
        visibility=AppVisibility.PUBLIC,
        capability_snapshot={"bindings": [binding.name]},
        csp_snapshot={"external_origins": []},
        snapshot_sha256="d" * 64,
        created_by="integration-test",
    )

    try:
        first_process = PostgresHostedAppsRepository(dsn)
        first_process.create_app_with_alias(app, "persistent-portal")
        first_process.save_binding(binding, actor="integration-test")
        first_process.save_collection(collection, actor="integration-test")
        first_process.add_deployment(deployment)
        first_process.publish_release(release)

        restarted_process = PostgresHostedAppsRepository(dsn)
        persisted = restarted_process.get_app(workspace.id, app.id)

        assert persisted.publication_state is PublicationState.PUBLISHED
        assert persisted.active_release_id == release.id
        assert (
            restarted_process.get_alias(workspace.id, app.id).alias
            == "persistent-portal"
        )
        assert (
            restarted_process.list_deployments(workspace.id, app.id)[0].id
            == deployment.id
        )
        assert restarted_process.list_bindings(workspace.id, app.id)[0].id == binding.id
        assert (
            restarted_process.list_collections(workspace.id, app.id)[0].id
            == collection.id
        )
        assert [
            event.action
            for event in restarted_process.list_audit_events(workspace.id, app.id)
        ] == [
            "app.create",
            "capability.binding.save",
            "capability.collection.save",
            "release.publish",
        ]
    finally:
        workspace_repository.delete_workspace(workspace.id)
        with connect(dsn) as conn:
            conn.execute(
                """
                DELETE FROM hosted_app_platform_audit_events
                 WHERE metadata @> %s
                """,
                (Jsonb({"workspace_id": str(workspace.id)}),),
            )


def test_postgres_runtime_survives_restart_and_serializes_concurrency() -> None:
    """Two runtime adapters share handles, idempotency, and concurrency leases."""
    if os.getenv("ORCHEO_TEST_POSTGRES_PERSISTENCE") != "1":
        pytest.skip("Postgres persistence integration checks are not enabled.")
    dsn = os.getenv("ORCHEO_POSTGRES_DSN")
    if dsn is None:
        pytest.fail("ORCHEO_POSTGRES_DSN is required for this integration check.")

    workspace_repository = PostgresWorkspaceRepository(dsn)
    workspace = Workspace(
        slug=f"hosted-runtime-{uuid4().hex[:12]}",
        name="Hosted Apps runtime test",
    )
    workspace_repository.create_workspace(workspace)
    app = HostedApp(
        workspace_id=workspace.id,
        name="Runtime portal",
        created_by="integration-test",
    )
    deployment = AppDeployment(
        workspace_id=workspace.id,
        app_id=app.id,
        status=DeploymentStatus.READY,
        created_by="integration-test",
    )
    binding = AppBinding(
        workspace_id=workspace.id,
        app_id=app.id,
        name="submit",
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        workflow_execution_sha256="c" * 64,
        access_mode="anonymous",
        output_projection={"fields": ["answer"]},
        visitor_can_read_output=True,
        limits={"max_concurrency": 1},
    )
    release = AppRelease(
        workspace_id=workspace.id,
        app_id=app.id,
        deployment_id=deployment.id,
        permission_revision=2,
        visibility=AppVisibility.PUBLIC,
        capability_snapshot={"bindings": []},
        csp_snapshot={},
        snapshot_sha256="d" * 64,
        created_by="integration-test",
    )
    repository = PostgresHostedAppsRepository(dsn)
    first_runtime = PostgresAppRuntimeService(dsn)
    second_runtime = PostgresAppRuntimeService(dsn)
    first_auth = PostgresAppAuthService(dsn)
    second_auth = PostgresAppAuthService(dsn)
    try:
        repository.create_app_with_alias(app, "runtime-portal")
        repository.save_binding(binding, actor="integration-test")
        repository.add_deployment(deployment)
        repository.publish_release(release)
        verifier = "v" * 64
        redirect_uri = "https://runtime-portal.apps.test/__orcheo/auth/callback"
        code = first_auth.issue_code(
            app_id=app.id,
            workspace_id=workspace.id,
            user_id="member-1",
            redirect_uri=redirect_uri,
            code_challenge=pkce_challenge(verifier),
        )
        issued = second_auth.exchange(
            raw_code=code,
            verifier=verifier,
            app_host="runtime-portal.apps.test",
            redirect_uri=redirect_uri,
            runtime_generation=1,
            current_member=True,
        )
        assert (
            first_auth.introspect(
                issued.secret,
                app_host="runtime-portal.apps.test",
                runtime_generation=1,
                current_member=True,
            ).user_id
            == "member-1"
        )
        common = {
            "workspace_id": workspace.id,
            "app_id": app.id,
            "release_id": release.id,
            "deployment_id": deployment.id,
            "binding_snapshot_sha256": release.snapshot_sha256,
            "runtime_generation": 1,
            "visitor_user_id": None,
            "session_id": None,
            "client_ip": "198.51.100.10",
        }
        accepted = first_runtime.accept(
            binding,
            payload={"message": "first"},
            idempotency_key="first",
            **common,
        )
        assert (
            second_runtime.status(
                accepted.handle,
                workspace_id=workspace.id,
                app_id=app.id,
                runtime_generation=1,
                visitor_user_id=None,
                session_id=None,
            ).status
            == "accepted"
        )
        with pytest.raises(AppRuntimeLimitError, match="concurrency"):
            second_runtime.accept(
                binding,
                payload={"message": "second"},
                idempotency_key="second",
                **common,
            )
        first_runtime.complete(accepted.handle, output={"answer": "done"})
        result = second_runtime.status(
            accepted.handle,
            workspace_id=workspace.id,
            app_id=app.id,
            runtime_generation=1,
            visitor_user_id=None,
            session_id=None,
        )
        assert result.status == "completed"
        assert result.output == {"answer": "done"}
        second_runtime.accept(
            binding,
            payload={"message": "second"},
            idempotency_key="second",
            **common,
        )
    finally:
        first_runtime.close()
        second_runtime.close()
        first_auth.close()
        second_auth.close()
        repository.close()
        workspace_repository.delete_workspace(workspace.id)
        with connect(dsn) as conn:
            conn.execute(
                """
                DELETE FROM hosted_app_platform_audit_events
                 WHERE metadata @> %s
                """,
                (Jsonb({"workspace_id": str(workspace.id)}),),
            )
