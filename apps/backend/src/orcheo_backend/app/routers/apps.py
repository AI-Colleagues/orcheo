"""Protected workspace-scoped Hosted Apps control-plane endpoints."""

from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from orcheo.hosted_apps import (
    AliasConflictError,
    AliasTombstonedError,
    AliasValidationError,
    AppBinding,
    AppCollection,
    AppDeployment,
    AppManifest,
    AppRelease,
    AppRuntimeError,
    AppVisibility,
    BundleValidationError,
    DeploymentService,
    DeploymentStatus,
    FilesystemBundleStore,
    HostedApp,
    HostedAppsRepository,
    ReservedAliasError,
    validate_input_schema,
)
from orcheo.hosted_apps.config import HostedAppsSettings, HostedAppsSettingsError
from orcheo.hosted_apps.zip_validation import BundleValidationLimits
from orcheo.workspace import Role, WorkspaceContext
from orcheo_backend.app.authentication import RequestContext, authenticate_request
from orcheo_backend.app.dependencies import get_repository
from orcheo_backend.app.hosted_apps import get_hosted_apps_repository
from orcheo_backend.app.repository import WorkflowRepository
from orcheo_backend.app.repository.errors import RepositoryError
from orcheo_backend.app.schemas.apps import (
    AppAliasRequest,
    AppAuditResponse,
    AppBindingRequest,
    AppBindingResponse,
    AppCollectionRequest,
    AppCollectionResponse,
    AppCreateRequest,
    AppDeploymentResponse,
    AppPublishRequest,
    AppPublishResponse,
    AppUpdateRequest,
    HostedAppListResponse,
    HostedAppResponse,
)
from orcheo_backend.app.workspace import WorkspaceContextDep, require_role


router = APIRouter(prefix="/apps", tags=["apps"])
RepositoryDep = Annotated[HostedAppsRepository, Depends(get_hosted_apps_repository)]
WorkflowRepositoryDep = Annotated[WorkflowRepository, Depends(get_repository)]


def _not_found() -> HTTPException:
    """Return the stable not-found response without cross-workspace disclosure."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "hosted_apps.not_found", "message": "App was not found."},
    )


def _encode_app_cursor(app: HostedApp) -> str:
    raw = f"{app.updated_at.isoformat()}|{app.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_app_cursor(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        timestamp, app_id = (
            base64.urlsafe_b64decode(cursor + padding).decode().split("|", 1)
        )
        UUID(app_id)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "hosted_apps.cursor_invalid",
                "message": "App cursor is invalid.",
            },
        ) from exc
    return timestamp, app_id


def _ensure_enabled(workspace: WorkspaceContextDep) -> None:
    """Keep all control-plane surfaces absent until config and allowlist permit them."""
    try:
        settings = HostedAppsSettings.from_environment()
    except HostedAppsSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "hosted_apps.configuration_invalid", "message": str(exc)},
        ) from exc
    if not settings.enabled or not settings.allows_workspace(
        str(workspace.workspace_id)
    ):
        raise _not_found()


def _binding_response(binding: AppBinding) -> AppBindingResponse:
    return AppBindingResponse(
        **binding.model_dump(include=set(AppBindingResponse.model_fields))
    )


def _collection_response(collection: AppCollection) -> AppCollectionResponse:
    return AppCollectionResponse(
        **collection.model_dump(include=set(AppCollectionResponse.model_fields))
    )


def _deployment_response(deployment: AppDeployment) -> AppDeploymentResponse:
    """Return only visitor-safe validation metadata."""
    return AppDeploymentResponse(
        id=deployment.id,
        status=deployment.status.value,
        archive_sha256=deployment.archive_sha256,
        manifest_sha256=deployment.manifest_sha256,
        app_manifest=deployment.app_manifest,
        validation_error_code=deployment.validation_error_code,
        validation_error_message=deployment.validation_error_message,
        created_at=deployment.created_at,
    )


async def _build_binding(
    request: AppBindingRequest,
    *,
    app_id: UUID,
    workspace_id: UUID,
    workflows: WorkflowRepository,
    binding_id: UUID | None = None,
) -> AppBinding:
    """Validate same-workspace workflow evidence and copy executable state."""
    validate_input_schema(request.input_schema)
    workflow = await workflows.get_workflow(
        request.workflow_id, workspace_id=str(workspace_id)
    )
    version = await workflows.get_version(request.workflow_version_id)
    if (
        version.workflow_id != workflow.id
        or version.workspace_id != str(workspace_id)
        or getattr(workflow, "is_archived", False)
    ):
        raise ValueError("Workflow version is not executable in this workspace.")
    runnable = version.runnable_config or {}
    executable = {
        "graph_sha256": version.compute_checksum(),
        "runnable_config": runnable,
    }
    digest = hashlib.sha256(
        json.dumps(executable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    values = {
        "workspace_id": workspace_id,
        "app_id": app_id,
        "name": request.name,
        "workflow_id": request.workflow_id,
        "workflow_version_id": request.workflow_version_id,
        "workflow_execution_sha256": digest,
        "runnable_config_snapshot": json.loads(json.dumps(runnable)),
        "access_mode": request.access_mode,
        "input_schema": request.input_schema,
        "output_projection": request.output_projection,
        "visitor_can_read_output": request.visitor_can_read_output,
        "visitor_can_read_sanitized_errors": (
            request.visitor_can_read_sanitized_errors
        ),
        "limits": request.limits,
    }
    if binding_id is not None:
        values["id"] = binding_id
    return AppBinding(**values)


async def _resolve_manifest_bindings(
    manifest: AppManifest,
    *,
    app_id: UUID,
    workspace_id: UUID,
    workflows: WorkflowRepository,
) -> list[AppBinding]:
    """Resolve portable workflow refs to exact same-workspace release grants."""
    resolved: list[AppBinding] = []
    for name, declaration in sorted(manifest.bindings.items()):
        try:
            workflow_id = await workflows.resolve_workflow_ref(
                declaration.workflow,
                include_archived=False,
                workspace_id=str(workspace_id),
            )
            version = await workflows.get_version_by_number(
                workflow_id, declaration.version
            )
            request = AppBindingRequest(
                name=name,
                workflow_id=workflow_id,
                workflow_version_id=version.id,
                **declaration.model_dump(exclude={"workflow", "version"}),
            )
            resolved.append(
                await _build_binding(
                    request,
                    app_id=app_id,
                    workspace_id=workspace_id,
                    workflows=workflows,
                )
            )
        except (RepositoryError, AppRuntimeError, ValueError) as exc:
            raise ValueError(
                f"Binding {name!r} could not resolve workflow "
                f"{declaration.workflow!r} version {declaration.version}."
            ) from exc
    return resolved


@router.get("", response_model=HostedAppListResponse)
async def list_apps(
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    _: Annotated[None, Depends(_ensure_enabled)],
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> HostedAppListResponse:
    """List apps only in the selected, authorized workspace."""
    apps = repository.list_apps(workspace.workspace_id)
    if cursor:
        timestamp, app_id = _decode_app_cursor(cursor)
        apps = [
            app
            for app in apps
            if (app.updated_at.isoformat(), str(app.id)) < (timestamp, app_id)
        ]
    page = apps[:limit]
    return HostedAppListResponse(
        apps=[
            HostedAppResponse.from_domain(
                app,
                alias=repository.get_alias(workspace.workspace_id, app.id).alias,
            )
            for app in page
        ],
        next_cursor=_encode_app_cursor(page[-1]) if len(apps) > limit else None,
    )


@router.post("", response_model=HostedAppResponse, status_code=status.HTTP_201_CREATED)
async def create_app(
    request: AppCreateRequest,
    repository: RepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.EDITOR))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> HostedAppResponse:
    """Atomically create an editor-owned draft app and its global alias reservation."""
    app = HostedApp(
        workspace_id=workspace.workspace_id,
        name=request.name,
        description=request.description,
        created_by=auth.subject,
    )
    try:
        repository.create_app_with_alias(app, request.alias)
    except (AliasConflictError, AliasTombstonedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except (AliasValidationError, ReservedAliasError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return HostedAppResponse.from_domain(app, alias=request.alias.strip().lower())


@router.get("/{app_id}", response_model=HostedAppResponse)
async def get_app(
    app_id: UUID,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    _: Annotated[None, Depends(_ensure_enabled)],
) -> HostedAppResponse:
    """Return one app only if it is owned by the selected workspace."""
    try:
        app = repository.get_app(workspace.workspace_id, app_id)
    except KeyError as exc:
        raise _not_found() from exc
    alias = repository.get_alias(workspace.workspace_id, app.id)
    return HostedAppResponse.from_domain(app, alias=alias.alias)


@router.patch("/{app_id}", response_model=HostedAppResponse)
async def update_app(
    app_id: UUID,
    request: AppUpdateRequest,
    repository: RepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.EDITOR))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> HostedAppResponse:
    """Update descriptive draft metadata; visibility requires an administrator."""
    try:
        app = repository.get_app(workspace.workspace_id, app_id)
    except KeyError as exc:
        raise _not_found() from exc
    if request.visibility is not None and not workspace.has_role(Role.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "hosted_apps.admin_required",
                "message": "An administrator is required to change app visibility.",
            },
        )
    if request.name is not None:
        app.name = request.name
    if request.description is not None:
        app.description = request.description
    if request.visibility is not None:
        app.visibility = AppVisibility(request.visibility)
        app.permission_revision += 1
    repository.update_app(app, actor=auth.subject)
    alias = repository.get_alias(workspace.workspace_id, app.id)
    return HostedAppResponse.from_domain(app, alias=alias.alias)


@router.post("/{app_id}/archive", response_model=HostedAppResponse)
async def archive_app(
    app_id: UUID,
    repository: RepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> HostedAppResponse:
    """Archive an app without releasing its alias or deleting immutable history."""
    try:
        app = repository.get_app(workspace.workspace_id, app_id)
    except KeyError as exc:
        raise _not_found() from exc
    app.is_archived = True
    repository.update_app(app, actor=auth.subject, action="app.archive")
    alias = repository.get_alias(workspace.workspace_id, app.id)
    return HostedAppResponse.from_domain(app, alias=alias.alias)


@router.post("/{app_id}/restore", response_model=HostedAppResponse)
async def restore_app(
    app_id: UUID,
    repository: RepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> HostedAppResponse:
    """Restore an archived app while retaining its prior publication lifecycle."""
    try:
        app = repository.get_app(workspace.workspace_id, app_id)
    except KeyError as exc:
        raise _not_found() from exc
    app.is_archived = False
    repository.update_app(app, actor=auth.subject, action="app.restore")
    alias = repository.get_alias(workspace.workspace_id, app.id)
    return HostedAppResponse.from_domain(app, alias=alias.alias)


@router.put("/{app_id}/alias", response_model=HostedAppResponse)
async def replace_alias(
    app_id: UUID,
    request: AppAliasRequest,
    repository: RepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> HostedAppResponse:
    """Reserve a replacement alias and tombstone the prior origin."""
    try:
        app = repository.get_app(workspace.workspace_id, app_id)
        alias = repository.reserve_alias(app, request.alias, actor=auth.subject)
    except KeyError as exc:
        raise _not_found() from exc
    except (AliasConflictError, AliasTombstonedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except (AliasValidationError, ReservedAliasError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return HostedAppResponse.from_domain(app, alias=alias.alias)


@router.get("/{app_id}/bindings", response_model=list[AppBindingResponse])
async def list_bindings(
    app_id: UUID,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    _: Annotated[None, Depends(_ensure_enabled)],
) -> list[AppBindingResponse]:
    """List the mutable draft grants without exposing active release internals."""
    try:
        items = repository.list_bindings(workspace.workspace_id, app_id)
    except KeyError as exc:
        raise _not_found() from exc
    return [_binding_response(item) for item in items]


@router.post(
    "/{app_id}/bindings",
    response_model=AppBindingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_binding(
    app_id: UUID,
    request: AppBindingRequest,
    repository: RepositoryDep,
    workflows: WorkflowRepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> AppBindingResponse:
    """Create a same-workspace binding with copied executable evidence."""
    try:
        repository.get_app(workspace.workspace_id, app_id)
        binding = await _build_binding(
            request,
            app_id=app_id,
            workspace_id=workspace.workspace_id,
            workflows=workflows,
        )
        saved = repository.save_binding(binding, actor=auth.subject)
    except KeyError as exc:
        raise _not_found() from exc
    except (AppRuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "hosted_apps.binding_invalid", "message": str(exc)},
        ) from exc
    return _binding_response(saved)


@router.put("/{app_id}/bindings/{binding_id}", response_model=AppBindingResponse)
async def update_binding(
    app_id: UUID,
    binding_id: UUID,
    request: AppBindingRequest,
    repository: RepositoryDep,
    workflows: WorkflowRepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> AppBindingResponse:
    """Replace a live draft binding while retaining its logical stable id."""
    try:
        existing_ids = {
            item.id for item in repository.list_bindings(workspace.workspace_id, app_id)
        }
        if binding_id not in existing_ids:
            raise KeyError(binding_id)
        binding = await _build_binding(
            request,
            app_id=app_id,
            workspace_id=workspace.workspace_id,
            workflows=workflows,
            binding_id=binding_id,
        )
        saved = repository.save_binding(binding, actor=auth.subject)
    except KeyError as exc:
        raise _not_found() from exc
    except (AppRuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "hosted_apps.binding_invalid", "message": str(exc)},
        ) from exc
    return _binding_response(saved)


@router.delete(
    "/{app_id}/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_binding(
    app_id: UUID,
    binding_id: UUID,
    repository: RepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> None:
    """Tombstone a draft binding without modifying existing releases."""
    try:
        repository.delete_binding(
            workspace.workspace_id, app_id, binding_id, actor=auth.subject
        )
    except KeyError as exc:
        raise _not_found() from exc


@router.get("/{app_id}/collections", response_model=list[AppCollectionResponse])
async def list_collections(
    app_id: UUID,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    _: Annotated[None, Depends(_ensure_enabled)],
) -> list[AppCollectionResponse]:
    """List stable live draft collection definitions."""
    try:
        items = repository.list_collections(workspace.workspace_id, app_id)
    except KeyError as exc:
        raise _not_found() from exc
    return [_collection_response(item) for item in items]


@router.get("/{app_id}/audit", response_model=list[AppAuditResponse])
async def list_app_audit(
    app_id: UUID,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    _: Annotated[None, Depends(_ensure_enabled)],
) -> list[AppAuditResponse]:
    """Return app-level audit evidence without platform-only metadata."""
    try:
        events = repository.list_audit_events(workspace.workspace_id, app_id)
    except KeyError as exc:
        raise _not_found() from exc
    return [
        AppAuditResponse(
            id=event.id,
            action=event.action,
            actor=event.actor,
            created_at=event.created_at,
        )
        for event in events
    ]


@router.post(
    "/{app_id}/collections",
    response_model=AppCollectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection(
    app_id: UUID,
    request: AppCollectionRequest,
    repository: RepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> AppCollectionResponse:
    """Create a stable-id collection with explicit visitor authorization."""
    try:
        repository.get_app(workspace.workspace_id, app_id)
        saved = repository.save_collection(
            AppCollection(
                workspace_id=workspace.workspace_id,
                app_id=app_id,
                **request.model_dump(),
            ),
            actor=auth.subject,
        )
    except KeyError as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "hosted_apps.collection_conflict", "message": str(exc)},
        ) from exc
    return _collection_response(saved)


@router.put(
    "/{app_id}/collections/{collection_id}",
    response_model=AppCollectionResponse,
)
async def update_collection(
    app_id: UUID,
    collection_id: UUID,
    request: AppCollectionRequest,
    repository: RepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> AppCollectionResponse:
    """Replace collection policy without changing its stable identity."""
    try:
        existing_ids = {
            item.id
            for item in repository.list_collections(workspace.workspace_id, app_id)
        }
        if collection_id not in existing_ids:
            raise KeyError(collection_id)
        saved = repository.save_collection(
            AppCollection(
                id=collection_id,
                workspace_id=workspace.workspace_id,
                app_id=app_id,
                **request.model_dump(),
            ),
            actor=auth.subject,
        )
    except KeyError as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "hosted_apps.collection_conflict", "message": str(exc)},
        ) from exc
    return _collection_response(saved)


@router.delete(
    "/{app_id}/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_collection(
    app_id: UUID,
    collection_id: UUID,
    repository: RepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> None:
    """Tombstone a collection so later name reuse gets a new stable scope."""
    try:
        repository.delete_collection(
            workspace.workspace_id, app_id, collection_id, actor=auth.subject
        )
    except KeyError as exc:
        raise _not_found() from exc


@router.get("/{app_id}/deployments", response_model=list[AppDeploymentResponse])
async def list_deployments(
    app_id: UUID,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    _: Annotated[None, Depends(_ensure_enabled)],
) -> list[AppDeploymentResponse]:
    """List validation status without exposing private object-store identifiers."""
    try:
        deployments = repository.list_deployments(workspace.workspace_id, app_id)
    except KeyError as exc:
        raise _not_found() from exc
    return [_deployment_response(item) for item in deployments]


@router.post(
    "/{app_id}/deployments/upload",
    response_model=AppDeploymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_local_deployment(
    app_id: UUID,
    bundle: Annotated[
        UploadFile,
        File(description="A prebuilt static ZIP with index.html at its root."),
    ],
    repository: RepositoryDep,
    workflows: WorkflowRepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.EDITOR))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> AppDeploymentResponse:
    """Validate and materialize one bounded local/single-node deployment upload."""
    try:
        repository.get_app(workspace.workspace_id, app_id)
    except KeyError as exc:
        raise _not_found() from exc
    settings = HostedAppsSettings.from_environment()
    if (
        settings.bundle_backend != "filesystem"
        or settings.filesystem_root is None
        or settings.deployment_mode not in {"local", "single-node"}
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "hosted_apps.upload_presign_required",
                "message": "This deployment requires the production upload flow.",
            },
        )
    if bundle.filename is None or not bundle.filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "hosted_apps.upload.archive_required",
                "message": "Deployment bundle must be a ZIP archive.",
            },
        )
    source = bundle.file
    source.seek(0, 2)
    archive_size = source.tell()
    source.seek(0)
    if archive_size <= 0 or archive_size > settings.max_archive_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "hosted_apps.bundle.archive_too_large",
                "message": (
                    "Deployment bundle is empty or exceeds the configured limit."
                ),
            },
        )
    service = DeploymentService(
        FilesystemBundleStore(Path(settings.filesystem_root)),
        limits=BundleValidationLimits(
            max_archive_bytes=settings.max_archive_bytes,
            max_expanded_bytes=settings.max_expanded_bytes,
            max_file_count=settings.max_file_count,
        ),
    )
    upload, deployment = service.initiate(
        workspace_id=workspace.workspace_id,
        app_id=app_id,
        created_by=auth.subject,
        expected_size_bytes=archive_size,
    )
    service.stage(upload.id, source)
    try:
        completed = service.complete(upload.id)
    except BundleValidationError as exc:
        failed = service.get_deployment(deployment.id)
        repository.add_deployment(failed)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if completed.app_manifest is not None:
        try:
            await _resolve_manifest_bindings(
                completed.app_manifest,
                app_id=app_id,
                workspace_id=workspace.workspace_id,
                workflows=workflows,
            )
        except ValueError as exc:
            completed.status = DeploymentStatus.FAILED
            completed.validation_error_code = "hosted_apps.bundle.binding_invalid"
            completed.validation_error_message = str(exc)
            repository.add_deployment(completed)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": completed.validation_error_code,
                    "message": completed.validation_error_message,
                },
            ) from exc
    repository.add_deployment(completed)
    return _deployment_response(completed)


@router.post(
    "/{app_id}/deployments/{deployment_id}/publish",
    response_model=AppPublishResponse,
)
async def publish_app(
    app_id: UUID,
    deployment_id: UUID,
    request: AppPublishRequest,
    repository: RepositoryDep,
    workflows: WorkflowRepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> AppPublishResponse:
    """Publish or roll back by creating and selecting a new immutable release."""
    try:
        app = repository.get_app(workspace.workspace_id, app_id)
        alias = repository.get_alias(workspace.workspace_id, app_id)
        deployment = next(
            item
            for item in repository.list_deployments(workspace.workspace_id, app_id)
            if item.id == deployment_id
        )
    except (KeyError, StopIteration) as exc:
        raise _not_found() from exc
    if deployment.app_manifest is not None:
        try:
            bindings = await _resolve_manifest_bindings(
                deployment.app_manifest,
                app_id=app_id,
                workspace_id=workspace.workspace_id,
                workflows=workflows,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "hosted_apps.binding_resolution_failed",
                    "message": str(exc),
                },
            ) from exc
    else:
        bindings = repository.list_bindings(workspace.workspace_id, app_id)
    snapshot = {
        "permission_revision": request.acknowledged_permission_revision,
        "visibility": app.visibility.value,
        "bindings": [
            item.model_dump(mode="json", exclude={"workspace_id", "app_id"})
            for item in bindings
        ],
        "collections": [
            item.model_dump(mode="json", exclude={"workspace_id", "app_id"})
            for item in repository.list_collections(workspace.workspace_id, app_id)
        ],
        "external_origins": list(app.external_origins),
    }
    snapshot_sha256 = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    release = AppRelease(
        workspace_id=workspace.workspace_id,
        app_id=app_id,
        deployment_id=deployment_id,
        permission_revision=request.acknowledged_permission_revision,
        visibility=app.visibility,
        capability_snapshot=snapshot,
        csp_snapshot={"external_origins": list(app.external_origins)},
        snapshot_sha256=snapshot_sha256,
        created_by=auth.subject,
    )
    try:
        published = repository.publish_release(release)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "hosted_apps.publish_conflict", "message": str(exc)},
        ) from exc
    settings = HostedAppsSettings.from_environment()
    return AppPublishResponse(
        app_id=app.id,
        active_release_id=release.id,
        active_deployment_id=deployment_id,
        published_permission_revision=release.permission_revision,
        state=published.derived_state,
        url=f"https://{alias.alias}.{settings.base_domain}",
    )


@router.post("/{app_id}/unpublish", response_model=HostedAppResponse)
async def unpublish_app(
    app_id: UUID,
    repository: RepositoryDep,
    workspace: Annotated[WorkspaceContext, Depends(require_role(Role.ADMIN))],
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    _: Annotated[None, Depends(_ensure_enabled)],
) -> HostedAppResponse:
    """Stop new delivery while preserving the last immutable release for rollback."""
    try:
        app = repository.unpublish(workspace.workspace_id, app_id, actor=auth.subject)
    except KeyError as exc:
        raise _not_found() from exc
    alias = repository.get_alias(workspace.workspace_id, app.id)
    return HostedAppResponse.from_domain(app, alias=alias.alias)
