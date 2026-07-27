"""Draft binding and stable collection lifecycle tests."""

from uuid import uuid4

import pytest

from orcheo.hosted_apps import (
    AppBinding,
    AppCollection,
    AppDeployment,
    AppRelease,
    AppRuntimeError,
    AppVisibility,
    DeploymentStatus,
    HostedApp,
    InMemoryHostedAppsRepository,
    validate_input_schema,
)


def _repository():
    repository = InMemoryHostedAppsRepository()
    app = HostedApp(workspace_id=uuid4(), name="Portal", created_by="editor")
    repository.create_app_with_alias(app, "capability-test")
    return repository, app


def test_binding_mutation_is_tenant_scoped_and_revises_draft() -> None:
    repository, app = _repository()
    binding = AppBinding(
        workspace_id=app.workspace_id,
        app_id=app.id,
        name="run",
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        workflow_execution_sha256="a" * 64,
        access_mode="anonymous",
    )
    repository.save_binding(binding, actor="editor")
    assert repository.get_app(app.workspace_id, app.id).permission_revision == 2
    with pytest.raises(KeyError):
        repository.list_bindings(uuid4(), app.id)
    repository.delete_binding(app.workspace_id, app.id, binding.id, actor="editor")
    assert repository.list_bindings(app.workspace_id, app.id) == []


def test_collection_name_reuse_never_reuses_stable_identity() -> None:
    repository, app = _repository()
    first = AppCollection(
        workspace_id=app.workspace_id,
        app_id=app.id,
        name="preferences",
        scope="user",
        read_access="authenticated",
        write_access="authenticated",
        max_document_bytes=1024,
        max_records=10,
    )
    repository.save_collection(first, actor="editor")
    repository.delete_collection(app.workspace_id, app.id, first.id, actor="editor")
    replacement = first.model_copy(update={"id": uuid4(), "deleted_at": None})
    repository.save_collection(replacement, actor="editor")
    assert replacement.id != first.id
    assert repository.list_collections(app.workspace_id, app.id)[0].id == replacement.id


def test_schema_definition_rejects_unsupported_or_invalid_keywords() -> None:
    validate_input_schema(
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        }
    )
    with pytest.raises(AppRuntimeError):
        validate_input_schema({"oneOf": [{"type": "string"}]})
    with pytest.raises(AppRuntimeError):
        validate_input_schema(
            {
                "type": "object",
                "properties": {},
                "required": ["missing"],
            }
        )


def test_draft_capability_change_invalidates_prior_publish_review() -> None:
    repository, app = _repository()
    deployment = AppDeployment(
        workspace_id=app.workspace_id,
        app_id=app.id,
        status=DeploymentStatus.READY,
        created_by="editor",
    )
    repository.add_deployment(deployment)
    collection = AppCollection(
        workspace_id=app.workspace_id,
        app_id=app.id,
        name="records",
        scope="shared",
        read_access="anonymous",
        write_access="authenticated",
        max_document_bytes=1024,
        max_records=10,
    )
    repository.save_collection(collection, actor="editor")
    stale_release = AppRelease(
        workspace_id=app.workspace_id,
        app_id=app.id,
        deployment_id=deployment.id,
        permission_revision=1,
        visibility=AppVisibility.PUBLIC,
        capability_snapshot={},
        csp_snapshot={},
        snapshot_sha256="e" * 64,
        created_by="admin",
    )
    with pytest.raises(ValueError, match="current permission revision"):
        repository.publish_release(stale_release)
