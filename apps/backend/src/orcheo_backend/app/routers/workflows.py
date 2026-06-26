"""Workflow CRUD and version management routes."""

from __future__ import annotations
import asyncio
import logging
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from orcheo.config import get_settings
from orcheo.graph.ingestion import ScriptIngestionError, ingest_langgraph_script
from orcheo.graph.ingestion.sandbox import uploads_allowed
from orcheo.models import (
    Workflow,
    WorkflowDraftAccess,
    WorkflowVersion,
)
from orcheo.runtime.configurable_schema import (
    ConfigurableSchemaError,
    split_configurable,
)
from orcheo.runtime.runnable_config import RunnableConfigModel
from orcheo.workflow.mermaid import (
    render_mermaid_from_graph_payload,
    render_mermaid_from_graph_payload_full_env,
)
from orcheo.workspace import WorkspaceNotFoundError
from orcheo_backend.app.authentication import (
    AuthorizationError,
    AuthorizationPolicy,
    RequestContext,
    get_authorization_policy,
)
from orcheo_backend.app.authentication.settings import load_auth_settings
from orcheo_backend.app.chatkit_runtime import resolve_chatkit_token_issuer
from orcheo_backend.app.chatkit_tokens import ChatKitSessionTokenIssuer
from orcheo_backend.app.dependencies import RepositoryDep
from orcheo_backend.app.errors import WorkspaceQuotaExceededError, raise_not_found
from orcheo_backend.app.managed_workflows import (
    ensure_managed_vibe_workflow,
)
from orcheo_backend.app.plugin_inventory import (
    missing_required_plugins,
    required_plugins_from_metadata,
)
from orcheo_backend.app.repository import (
    CronTriggerNotFoundError,
    TeamNotFoundError,
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowPublishStateError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.schemas.chatkit import ChatKitSessionResponse
from orcheo_backend.app.schemas.workflows import (
    PublicWorkflow,
    WorkflowCreateRequest,
    WorkflowListItem,
    WorkflowPagePayload,
    WorkflowPageVersionSummary,
    WorkflowPublishRequest,
    WorkflowPublishResponse,
    WorkflowPublishRevokeRequest,
    WorkflowUpdateRequest,
    WorkflowVersionDiffResponse,
    WorkflowVersionIngestRequest,
    WorkflowVersionRunnableConfigUpdateRequest,
)
from orcheo_backend.app.teams_service import ensure_default_team
from orcheo_backend.app.workspace import (
    WorkspaceContextDep,
    WorkspaceServiceDep,
    get_workspace_repository,
)
from orcheo_backend.app.workspace_governance import ensure_workspace_workflow_quota
from orcheo_sdk.cli.workflow.frontmatter import parse_workflow_frontmatter


router = APIRouter()
public_router = APIRouter()
logger = logging.getLogger(__name__)


def _normalize_workspace_id(value: str) -> str:
    """Normalize workspace identifiers for case-insensitive comparisons."""
    return value.strip().lower()


def _merge_frontmatter_avatar(script: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Return metadata with avatar/subtitle filled in from script frontmatter.

    Only adds fields that are absent from the caller-supplied metadata, so an
    explicit ``avatar`` or ``subtitle`` in the request always wins.
    """
    if "avatar" in metadata and "subtitle" in metadata:
        return metadata
    try:
        fm = parse_workflow_frontmatter(script)
    except Exception:
        return metadata
    merged = dict(metadata)
    if fm.avatar and "avatar" not in merged:
        merged["avatar"] = fm.avatar
    if fm.subtitle and "subtitle" not in merged:
        merged["subtitle"] = fm.subtitle
    return merged


def _resolve_studio_url() -> str | None:
    settings = get_settings()
    value = settings.get("STUDIO_URL")
    if not value:
        return None
    return str(value).rstrip("/")


def _apply_share_url(
    workflow: Workflow,
    public_base_url: str | None,
    *,
    team_slug: str | None = None,
    workspace_slug: str | None = None,
) -> Workflow:
    if public_base_url and workflow.is_public:
        ref = workflow.handle or str(workflow.id)
        if workspace_slug and team_slug:
            workflow.share_url = (
                f"{public_base_url}/chat/{workspace_slug}/team/{team_slug}/{ref}"
            )
        elif workspace_slug:
            workflow.share_url = f"{public_base_url}/chat/{workspace_slug}/{ref}"
        elif team_slug:
            workflow.share_url = f"{public_base_url}/chat/team/{team_slug}/{ref}"
        else:
            workflow.share_url = f"{public_base_url}/chat/{ref}"
    else:
        workflow.share_url = None
    return workflow


async def _resolve_team_slug(repository: Any, workflow: Workflow) -> str | None:
    """Look up the slug for the team a workflow belongs to, or None."""
    if not workflow.team_id:
        return None
    try:
        team = await repository.get_team(
            UUID(workflow.team_id), workspace_id=workflow.workspace_id
        )
        return team.slug
    except Exception:  # noqa: BLE001
        return None


async def _apply_share_url_async(
    workflow: Workflow,
    repository: Any,
    public_base_url: str | None,
    *,
    workspace_slug: str | None = None,
) -> Workflow:
    """Resolve team slug then apply share URL — use for single-workflow responses."""
    team_slug = await _resolve_team_slug(repository, workflow)
    return _apply_share_url(
        workflow, public_base_url, team_slug=team_slug, workspace_slug=workspace_slug
    )


def _apply_share_urls(
    workflows: list[Workflow],
    public_base_url: str | None,
    *,
    teams_by_id: dict[str, str] | None = None,
    workspace_slug: str | None = None,
) -> list[Workflow]:
    for workflow in workflows:
        team_slug = (
            teams_by_id.get(workflow.team_id)
            if teams_by_id and workflow.team_id
            else None
        )
        _apply_share_url(
            workflow,
            public_base_url,
            team_slug=team_slug,
            workspace_slug=workspace_slug,
        )
    return workflows


def _required_plugins_from_metadata(metadata: dict[str, Any]) -> list[str]:
    """Extract template plugin prerequisites from workflow-version metadata."""
    return required_plugins_from_metadata(metadata)


def _serialize_runnable_config(
    runnable_config: RunnableConfigModel | None,
) -> dict[str, Any] | None:
    """Normalize runnable config payloads for storage."""
    if runnable_config is None:
        return None
    return runnable_config.model_dump(
        mode="json",
        exclude_defaults=True,
        exclude_none=True,
    )


def _merge_configurable_schema(
    existing: Any,
    inline: dict[str, Any],
) -> dict[str, Any]:
    """Merge inline schema declarations with caller-supplied schema metadata.

    A sibling ``*.schema.json`` file (delivered via ``metadata``) is authored
    explicitly, so it wins over annotations inferred from the runnable config.
    """
    if isinstance(existing, dict):
        return {**inline, **existing}
    return inline


def _apply_configurable_schema_order(metadata: dict[str, Any]) -> dict[str, Any]:
    """Record the authored key order of ``configurable_schema`` as an array.

    ``configurable_schema`` is persisted inside a JSONB column, which normalizes
    object key order and therefore loses the field order declared in the source
    ``config.json``. Capturing the order in a sibling list (arrays keep their
    order in JSONB) lets the workflow page render configurable fields in their
    authored sequence. A caller-supplied order is left untouched.
    """
    schema = metadata.get("configurable_schema")
    if not isinstance(schema, dict) or not schema:
        return metadata
    if isinstance(metadata.get("configurable_schema_order"), list):
        return metadata
    return {**metadata, "configurable_schema_order": list(schema.keys())}


def _resolve_ingest_configurable_schema(
    runnable_config: RunnableConfigModel | None,
    metadata: dict[str, Any],
) -> tuple[RunnableConfigModel | None, dict[str, Any]]:
    """Lift inline ``configurable`` schema annotations into version metadata."""
    if runnable_config is None or not runnable_config.configurable:
        return runnable_config, metadata
    try:
        resolved, inline_schema = split_configurable(runnable_config.configurable)
    except ConfigurableSchemaError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if not inline_schema:
        return runnable_config, metadata
    runnable_config = runnable_config.model_copy(update={"configurable": resolved})
    metadata = {
        **metadata,
        "configurable_schema": _merge_configurable_schema(
            metadata.get("configurable_schema"), inline_schema
        ),
    }
    return runnable_config, metadata


def _serialize_public_workflow(
    workflow: Workflow,
    public_base_url: str | None,
    *,
    team_slug: str | None = None,
    workspace_slug: str | None = None,
) -> PublicWorkflow:
    workflow = _apply_share_url(
        workflow, public_base_url, team_slug=team_slug, workspace_slug=workspace_slug
    )
    return PublicWorkflow(
        id=workflow.id,
        handle=workflow.handle,
        name=workflow.name,
        description=workflow.description,
        is_public=workflow.is_public,
        require_login=workflow.require_login,
        share_url=workflow.share_url,
        chatkit=workflow.chatkit,
    )


def _chatkit_update_kwargs(request: WorkflowUpdateRequest) -> dict[str, Any]:
    """Return repository update kwargs derived from ChatKit request fields."""
    update_kwargs: dict[str, Any] = {}
    if request.chatkit is not None:
        chatkit_fields = request.chatkit.model_fields_set
        if "start_screen_prompts" in chatkit_fields:
            if request.chatkit.start_screen_prompts is None:
                update_kwargs["clear_chatkit_start_screen_prompts"] = True
            else:
                update_kwargs["chatkit_start_screen_prompts"] = (
                    request.chatkit.start_screen_prompts
                )
        if "supported_models" in chatkit_fields:
            if request.chatkit.supported_models is None:
                update_kwargs["clear_chatkit_supported_models"] = True
            else:
                update_kwargs["chatkit_supported_models"] = (
                    request.chatkit.supported_models
                )
    if request.clear_chatkit_start_screen_prompts:
        update_kwargs["clear_chatkit_start_screen_prompts"] = True
    if request.clear_chatkit_supported_models:
        update_kwargs["clear_chatkit_supported_models"] = True
    return update_kwargs


def _attach_mermaid(version: WorkflowVersion) -> WorkflowVersion:
    """Attach Mermaid output to a workflow version payload."""
    mermaid: str | None = None
    graph = version.graph
    if isinstance(graph, dict):
        index = graph.get("index")
        if isinstance(index, dict):
            index_mermaid = index.get("mermaid")
            if isinstance(index_mermaid, str) and index_mermaid.strip():
                mermaid = index_mermaid

    if mermaid is None:
        mermaid = render_mermaid_from_graph_payload(version.graph or {})
    return version.model_copy(update={"mermaid": mermaid})


def _attach_mermaid_many(versions: list[WorkflowVersion]) -> list[WorkflowVersion]:
    """Attach Mermaid output to a list of workflow versions."""
    return [_attach_mermaid(version) for version in versions]


def _extract_index_mermaid(graph: Any) -> str | None:
    """Return precomputed Mermaid output without regenerating it."""
    if not isinstance(graph, dict):
        return None
    index = graph.get("index")
    if not isinstance(index, dict):
        return None
    mermaid = index.get("mermaid")
    if not isinstance(mermaid, str) or not mermaid.strip():
        return None
    return mermaid


def _has_indexed_cron_trigger(index: Any) -> bool:
    """Return True when graph index metadata contains cron entries."""
    if not isinstance(index, dict):
        return False
    cron_entries = index.get("cron")
    return isinstance(cron_entries, list) and len(cron_entries) > 0


def _has_cron_trigger_node(nodes: Any) -> bool:
    """Return True when a node list contains a cron trigger node."""
    if not isinstance(nodes, list):
        return False
    return any(
        isinstance(node, dict) and node.get("type") == "CronTriggerNode"
        for node in nodes
    )


def _graph_has_cron_trigger(graph: Any) -> bool:
    """Return True when the graph metadata includes a cron trigger."""
    if not isinstance(graph, dict):
        return False
    if _has_indexed_cron_trigger(graph.get("index")):
        return True
    if _has_cron_trigger_node(graph.get("nodes")):
        return True
    summary = graph.get("summary")
    if not isinstance(summary, dict):
        return False
    return _has_cron_trigger_node(summary.get("nodes"))


def _to_workflow_page_version_summary(
    version: WorkflowVersion,
) -> WorkflowPageVersionSummary:
    """Serialize a compact version record for opening the workflow page."""
    return WorkflowPageVersionSummary(
        id=version.id,
        workflow_id=version.workflow_id,
        version=version.version,
        mermaid=(
            _extract_index_mermaid(version.graph)
            or render_mermaid_from_graph_payload(version.graph or {})
        ),
        has_cron_trigger=_graph_has_cron_trigger(version.graph),
        metadata=version.metadata,
        runnable_config=version.runnable_config,
        notes=version.notes,
        created_by=version.created_by,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


async def _resolve_workflow_id(
    repository: RepositoryDep,
    workflow_ref: str,
    *,
    include_archived: bool = True,
    workspace_id: str | None = None,
    team_id: str | None = None,
) -> str:
    try:
        workflow_id = await repository.resolve_workflow_ref(
            workflow_ref,
            include_archived=include_archived,
            workspace_id=workspace_id,
            team_id=team_id,
        )
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    return str(workflow_id)


async def _resolve_workflow_uuid(
    repository: RepositoryDep,
    workflow_ref: str,
    *,
    include_archived: bool = True,
    workspace_id: str | None = None,
    team_id: str | None = None,
) -> UUID:
    workflow_id = await _resolve_workflow_id(
        repository,
        workflow_ref,
        include_archived=include_archived,
        workspace_id=workspace_id,
        team_id=team_id,
    )
    return UUID(workflow_id)


async def _load_workflow_for_request(
    repository: RepositoryDep,
    workflow_ref: str,
    *,
    include_archived: bool = True,
    workspace_id: str | None = None,
) -> Workflow:
    """Resolve and load a workflow, allowing managed workflows to cross workspaces."""
    workflow_id = await _resolve_workflow_uuid(
        repository,
        workflow_ref,
        include_archived=include_archived,
        workspace_id=workspace_id,
    )
    try:
        workflow = await repository.get_workflow(
            workflow_id,
            workspace_id=workspace_id,
        )
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)

    return workflow


async def _get_workflow_latest_version_summary(
    repository: RepositoryDep,
    workflow_id: UUID,
) -> WorkflowVersion | None:
    """Fetch latest version metadata for list responses."""
    try:
        return _attach_mermaid(await repository.get_latest_version(workflow_id))
    except (WorkflowNotFoundError, WorkflowVersionNotFoundError):
        return None


async def _get_workflow_schedule_summary(
    repository: RepositoryDep,
    workflow_id: UUID,
) -> bool:
    """Return whether a workflow currently has a cron schedule."""
    try:
        await repository.get_cron_trigger_config(workflow_id)
        return True
    except (WorkflowNotFoundError, CronTriggerNotFoundError):
        return False


async def _build_workflow_list_item(
    repository: RepositoryDep,
    workflow: Workflow,
) -> WorkflowListItem:
    """Build a list item by fetching workflow summaries concurrently."""
    latest_version, is_scheduled = await asyncio.gather(
        _get_workflow_latest_version_summary(repository, workflow.id),
        _get_workflow_schedule_summary(repository, workflow.id),
    )
    return WorkflowListItem(
        **workflow.model_dump(),
        latest_version=latest_version,
        is_scheduled=is_scheduled,
    )


def _resolve_workspace_id_from_slug(
    workspace_service: Any, workspace_slug: str
) -> str | None:
    """Resolve workspace ID from slug, returning None if not found."""
    try:
        workspace = workspace_service.repository.get_workspace_by_slug(workspace_slug)
        return str(workspace.id)
    except (WorkspaceNotFoundError, Exception):
        return None


async def _resolve_team_id_from_slug(
    repository: Any, team_slug: str, workspace_id: str
) -> str | None:
    """Resolve team ID from slug within workspace, returning None if not found."""
    try:
        team = await repository.get_team_by_slug(team_slug, workspace_id=workspace_id)
        return str(team.id)
    except (TeamNotFoundError, Exception):
        return None


@public_router.get("/workflows/{workflow_ref}/public", response_model=PublicWorkflow)
async def get_public_workflow(
    workflow_ref: str,
    repository: RepositoryDep,
    workspace_service: WorkspaceServiceDep,
    workspace_slug: str | None = Query(None),
    team_slug: str | None = Query(None),
) -> PublicWorkflow:
    """Fetch public workflow metadata without authentication.

    When workspace_slug and/or team_slug are provided, workflow resolution
    will be scoped to the specified workspace and team context.
    """
    # Resolve workspace and team IDs from slugs if provided
    resolved_workspace_id: str | None = None
    resolved_team_id: str | None = None

    if workspace_slug:
        resolved_workspace_id = _resolve_workspace_id_from_slug(
            workspace_service, workspace_slug
        )
        if not resolved_workspace_id:
            raise_not_found(
                "Workspace not found", WorkspaceNotFoundError(workspace_slug)
            )

        if team_slug:
            resolved_team_id = await _resolve_team_id_from_slug(
                repository, team_slug, resolved_workspace_id
            )
            if not resolved_team_id:
                raise_not_found("Team not found", TeamNotFoundError(team_slug))

    # Resolve workflow with team and workspace context
    workflow_id = await _resolve_workflow_uuid(
        repository,
        workflow_ref,
        workspace_id=resolved_workspace_id,
        team_id=resolved_team_id,
    )
    try:
        workflow = await repository.get_workflow(workflow_id)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    if workflow.is_archived:
        raise_not_found("Workflow not found", WorkflowNotFoundError(str(workflow_id)))
    if not workflow.is_public:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Workflow is not published.",
                "code": "workflow.not_public",
            },
        )
    team_slug_resolved = await _resolve_team_slug(repository, workflow)
    workspace_slug_resolved: str | None = None
    if workflow.workspace_id:
        try:
            ws = get_workspace_repository().get_workspace(UUID(workflow.workspace_id))
            workspace_slug_resolved = ws.slug
        except Exception:  # noqa: BLE001
            pass
    return _serialize_public_workflow(
        workflow,
        _resolve_studio_url(),
        team_slug=team_slug_resolved,
        workspace_slug=workspace_slug_resolved,
    )


@router.get("/workflows", response_model=list[WorkflowListItem])
async def list_workflows(
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    include_archived: bool = False,
) -> list[WorkflowListItem]:
    """Return workflows with latest-version and schedule summaries."""
    workspace_record = get_workspace_repository().get_workspace(workspace.workspace_id)
    managed_workflow = None
    try:
        managed_workflow = await ensure_managed_vibe_workflow(
            repository, workspace_record
        )
    except (RuntimeError, Exception):
        # Managed vibe workflow may not exist in production mode.
        pass
    workflows = await repository.list_workflows(
        include_archived=include_archived,
        workspace_id=str(workspace.workspace_id),
    )
    if (
        managed_workflow is not None
        and all(workflow.id != managed_workflow.id for workflow in workflows)
        and (include_archived or not managed_workflow.is_archived)
    ):
        workflows.append(managed_workflow)
    public_base_url = _resolve_studio_url()
    teams = await repository.list_teams(workspace_id=str(workspace.workspace_id))
    teams_by_id = {str(team.id): team.slug for team in teams}
    return await asyncio.gather(
        *[
            _build_workflow_list_item(repository, workflow)
            for workflow in _apply_share_urls(
                workflows,
                public_base_url,
                teams_by_id=teams_by_id,
                workspace_slug=workspace.workspace_slug,
            )
        ]
    )


@router.post(
    "/workflows",
    response_model=Workflow,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow(
    request: WorkflowCreateRequest,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    policy: AuthorizationPolicy = Depends(get_authorization_policy),  # noqa: B008
) -> Workflow:
    """Create a new workflow entry."""
    context = _resolve_authenticated_context(policy)
    actor = _resolve_actor(request.actor, context)
    tags = _append_workspace_tags(request.tags, context)
    draft_access = _resolve_draft_access(request.draft_access, tags, context)

    # Resolve the target team. When unspecified, place the workflow in the
    # workspace's default team rather than leaving it team-less.
    workspace_id = str(workspace.workspace_id)
    if request.team_id is not None:
        try:
            parsed_team_id = UUID(request.team_id)
            await repository.get_team(parsed_team_id, workspace_id=workspace_id)
        except (TeamNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "message": f"Team '{request.team_id}' not found.",
                    "code": "team.not_found",
                },
            ) from exc
        target_team_id = str(parsed_team_id)
    else:
        default_team = await ensure_default_team(repository, workspace)
        target_team_id = str(default_team.id)

    try:
        await ensure_workspace_workflow_quota(repository, workspace)
        create_kwargs: dict[str, Any] = {
            "name": request.name,
            "slug": request.slug,
            "description": request.description,
            "tags": tags,
            "draft_access": draft_access,
            "actor": actor,
            "workspace_id": workspace_id,
            "team_id": target_team_id,
        }
        if request.handle is not None:
            create_kwargs["handle"] = request.handle
        workflow = await repository.create_workflow(
            **create_kwargs,
        )
        return await _apply_share_url_async(
            workflow,
            repository,
            _resolve_studio_url(),
            workspace_slug=workspace.workspace_slug,
        )
    except WorkflowHandleConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "code": "workflow.handle.conflict"},
        ) from exc
    except WorkspaceQuotaExceededError as exc:
        raise exc.as_http_exception() from exc


@router.get("/workflows/{workflow_ref}", response_model=Workflow)
async def get_workflow(
    workflow_ref: str,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> Workflow:
    """Fetch a single workflow by its identifier."""
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    return await _apply_share_url_async(
        workflow,
        repository,
        _resolve_studio_url(),
        workspace_slug=workspace.workspace_slug,
    )


@router.get("/workflows/{workflow_ref}/workflow", response_model=WorkflowPagePayload)
async def get_workflow_page(
    workflow_ref: str,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> WorkflowPagePayload:
    """Fetch workflow metadata and compact version summaries for the workflow page."""
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    versions = await repository.list_versions(workflow.id)
    return WorkflowPagePayload(
        workflow=await _apply_share_url_async(
            workflow,
            repository,
            _resolve_studio_url(),
            workspace_slug=workspace.workspace_slug,
        ),
        versions=[_to_workflow_page_version_summary(version) for version in versions],
    )


@router.put("/workflows/{workflow_ref}", response_model=Workflow)
async def update_workflow(
    workflow_ref: str,
    request: WorkflowUpdateRequest,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    policy: AuthorizationPolicy = Depends(get_authorization_policy),  # noqa: B008
) -> Workflow:
    """Update attributes of an existing workflow."""
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    context = _resolve_authenticated_context(policy)
    actor = _resolve_actor(request.actor, context)
    tags = _append_workspace_tags(request.tags, context, preserve_none=True)
    draft_access_tags = tags
    if request.draft_access is not None and draft_access_tags is None:
        draft_access_tags = workflow.tags
    draft_access = (
        _resolve_draft_access(request.draft_access, draft_access_tags, context)
        if request.draft_access is not None
        else None
    )

    try:
        update_kwargs: dict[str, Any] = {
            "name": request.name,
            "description": request.description,
            "tags": tags,
            "draft_access": draft_access,
            "is_archived": request.is_archived,
            "actor": actor,
            **_chatkit_update_kwargs(request),
        }
        if request.handle is not None:
            update_kwargs["handle"] = request.handle
        workflow = await repository.update_workflow(
            workflow.id,
            **update_kwargs,
        )
        return await _apply_share_url_async(
            workflow,
            repository,
            _resolve_studio_url(),
            workspace_slug=workspace.workspace_slug,
        )
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowHandleConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "code": "workflow.handle.conflict"},
        ) from exc


@router.delete("/workflows/{workflow_ref}", response_model=Workflow)
async def archive_workflow(
    workflow_ref: str,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    actor: str = Query("system"),
    policy: AuthorizationPolicy = Depends(get_authorization_policy),  # noqa: B008
) -> Workflow:
    """Archive a workflow via the delete verb."""
    tid = str(workspace.workspace_id)
    context = _resolve_authenticated_context(policy)
    resolved_actor = _resolve_actor(actor, context)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    workflow = await repository.archive_workflow(workflow.id, actor=resolved_actor)
    return await _apply_share_url_async(
        workflow,
        repository,
        _resolve_studio_url(),
        workspace_slug=workspace.workspace_slug,
    )


@router.post(
    "/workflows/{workflow_ref}/versions/ingest",
    response_model=WorkflowVersion,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_workflow_version(
    workflow_ref: str,
    request: WorkflowVersionIngestRequest,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> WorkflowVersion:
    """Create a workflow version from a LangGraph Python script."""
    if not uploads_allowed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": (
                    "Workflow script ingestion is disabled in managed mode. "
                    "Use the Candidates tab to onboard AI colleagues, or set "
                    "ORCHEO_WORKFLOW_TRUST_MODE=self_host_unsafe for "
                    "self-hosted deployments."
                ),
                "code": "workflow.ingestion.disabled",
            },
        )
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    required_plugins = _required_plugins_from_metadata(request.metadata)
    missing_plugins = missing_required_plugins(required_plugins)
    if missing_plugins:
        plugin_list = ", ".join(missing_plugins)
        noun = "plugin" if len(missing_plugins) == 1 else "plugins"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Missing required {noun} for this template: {plugin_list}. "
                "Install them into the runtime before importing the template."
            ),
        )
    try:
        graph_payload = ingest_langgraph_script(
            request.script,
            entrypoint=request.entrypoint,
        )
    except ScriptIngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Pre-compute mermaid using the full Python environment and store it in the
    # graph index so the workflow page can read it without re-executing the script.
    mermaid = render_mermaid_from_graph_payload_full_env(graph_payload)
    if mermaid and isinstance(graph_payload.get("index"), dict):
        graph_payload["index"]["mermaid"] = mermaid

    runnable_config, metadata = _resolve_ingest_configurable_schema(
        request.runnable_config,
        request.metadata,
    )
    metadata = _merge_frontmatter_avatar(request.script, metadata)
    metadata = _apply_configurable_schema_order(metadata)

    try:
        version = await repository.create_version(
            workflow.id,
            graph=graph_payload,
            metadata=metadata,
            notes=request.notes,
            created_by=request.created_by,
            runnable_config=_serialize_runnable_config(runnable_config),
        )
        return _attach_mermaid(version)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)


@router.put(
    "/workflows/{workflow_ref}/versions/{version_number}/runnable-config",
    response_model=WorkflowVersion,
)
async def update_workflow_version_runnable_config(
    workflow_ref: str,
    version_number: int,
    request: WorkflowVersionRunnableConfigUpdateRequest,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> WorkflowVersion:
    """Update runnable config for an existing workflow version."""
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    try:
        version = await repository.update_version_runnable_config(
            workflow.id,
            version_number=version_number,
            runnable_config=_serialize_runnable_config(request.runnable_config),
            actor=request.actor,
        )
        return _attach_mermaid(version)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowVersionNotFoundError as exc:
        raise_not_found("Workflow version not found", exc)


@router.get(
    "/workflows/{workflow_ref}/versions",
    response_model=list[WorkflowVersion],
)
async def list_workflow_versions(
    workflow_ref: str,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> list[WorkflowVersion]:
    """Return the versions associated with a workflow."""
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    versions = await repository.list_versions(workflow.id)
    return _attach_mermaid_many(versions)


@router.get(
    "/workflows/{workflow_ref}/versions/{version_number}",
    response_model=WorkflowVersion,
)
async def get_workflow_version(
    workflow_ref: str,
    version_number: int,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> WorkflowVersion:
    """Return a specific workflow version by number."""
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    try:
        version = await repository.get_version_by_number(workflow.id, version_number)
        return _attach_mermaid(version)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowVersionNotFoundError as exc:
        raise_not_found("Workflow version not found", exc)


@router.get(
    "/workflows/{workflow_ref}/versions/{version_number}/mermaid",
    response_model=dict,
)
async def get_workflow_version_mermaid(
    workflow_ref: str,
    version_number: int,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> dict:
    """Render a Mermaid diagram from a stored workflow version."""
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    try:
        version = await repository.get_version_by_number(workflow.id, version_number)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowVersionNotFoundError as exc:
        raise_not_found("Workflow version not found", exc)
    mermaid = render_mermaid_from_graph_payload(version.graph or {})
    if mermaid is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Mermaid cannot be rendered for this workflow version.",
        )
    return {"mermaid": mermaid}


@router.get(
    "/workflows/{workflow_ref}/versions/{base_version}/diff/{target_version}",
    response_model=WorkflowVersionDiffResponse,
)
async def diff_workflow_versions(
    workflow_ref: str,
    base_version: int,
    target_version: int,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> WorkflowVersionDiffResponse:
    """Generate a diff between two workflow versions."""
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    try:
        diff = await repository.diff_versions(workflow.id, base_version, target_version)
        return WorkflowVersionDiffResponse(
            base_version=diff.base_version,
            target_version=diff.target_version,
            diff=diff.diff,
        )
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowVersionNotFoundError as exc:
        raise_not_found("Workflow version not found", exc)


def _publish_response(
    workflow: Workflow,
    *,
    message: str | None = None,
) -> WorkflowPublishResponse:
    return WorkflowPublishResponse(
        workflow=workflow,
        message=message,
        share_url=workflow.share_url,
    )


@router.post(
    "/workflows/{workflow_ref}/publish",
    response_model=WorkflowPublishResponse,
    status_code=status.HTTP_201_CREATED,
)
async def publish_workflow(
    workflow_ref: str,
    request: WorkflowPublishRequest,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    policy: AuthorizationPolicy = Depends(get_authorization_policy),  # noqa: B008
) -> WorkflowPublishResponse:
    """Publish a workflow and expose it for ChatKit access."""
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    context = _resolve_authenticated_context(policy)
    actor = _resolve_actor(request.actor, context)

    try:
        workflow = await repository.publish_workflow(
            workflow.id,
            require_login=request.require_login,
            actor=actor,
        )
        workflow = await _apply_share_url_async(
            workflow,
            repository,
            _resolve_studio_url(),
            workspace_slug=workspace.workspace_slug,
        )
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowPublishStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "code": "workflow.publish.invalid_state"},
        ) from exc

    logger.info(
        "Workflow published",
        extra={
            "workflow_id": str(workflow.id),
            "actor": actor,
            "require_login": request.require_login,
        },
    )
    return _publish_response(
        workflow,
        message="Workflow is now public via the /chat route.",
    )


@router.post(
    "/workflows/{workflow_ref}/publish/revoke",
    response_model=Workflow,
)
async def revoke_workflow_publish(
    workflow_ref: str,
    request: WorkflowPublishRevokeRequest,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    policy: AuthorizationPolicy = Depends(get_authorization_policy),  # noqa: B008
) -> Workflow:
    """Revoke public access to the workflow."""
    tid = str(workspace.workspace_id)
    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    context = _resolve_authenticated_context(policy)
    actor = _resolve_actor(request.actor, context)

    try:
        workflow = await repository.revoke_publish(workflow.id, actor=actor)
        workflow = await _apply_share_url_async(
            workflow,
            repository,
            _resolve_studio_url(),
            workspace_slug=workspace.workspace_slug,
        )
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowPublishStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "code": "workflow.publish.invalid_state"},
        ) from exc

    logger.info(
        "Workflow publish access revoked",
        extra={
            "workflow_id": str(workflow.id),
            "actor": actor,
        },
    )

    return workflow


def _select_primary_workspace(workspace_ids: frozenset[str]) -> str | None:
    if len(workspace_ids) == 1:
        return next(iter(workspace_ids))
    return None


def _extract_workflow_workspace_ids(workflow: Workflow) -> frozenset[str]:
    """Return workspace identifiers encoded within workflow tags."""
    workspaces = {
        _normalize_workspace_id(tag.split(":", 1)[1])
        for tag in workflow.tags
        if tag.lower().startswith("workspace:") and ":" in tag
    }
    return frozenset(workspaces)


def _resolve_draft_access(
    requested_draft_access: WorkflowDraftAccess | None,
    tags: list[str] | None,
    context: RequestContext | None,
) -> WorkflowDraftAccess:
    """Resolve draft access from explicit input, tags, and auth context."""
    has_workspace_tags = bool(
        tags
        and any(tag.lower().startswith("workspace:") and ":" in tag for tag in tags)
    )
    if requested_draft_access is not None:
        if (
            requested_draft_access is WorkflowDraftAccess.PERSONAL
            and has_workspace_tags
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": (
                        "Personal draft workflows cannot include workspace tags."
                    ),
                    "code": "workflow.draft_access.conflict",
                },
            )
        if (
            requested_draft_access is WorkflowDraftAccess.WORKSPACE
            and not has_workspace_tags
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": (
                        "Workspace draft workflows require at least one workspace tag."
                    ),
                    "code": "workflow.draft_access.workspace_required",
                },
            )
        return requested_draft_access

    if context is not None and context.identity_type == "user":
        return WorkflowDraftAccess.AUTHENTICATED
    if has_workspace_tags:
        return WorkflowDraftAccess.WORKSPACE
    if context is not None and context.workspace_ids:
        return WorkflowDraftAccess.WORKSPACE
    return WorkflowDraftAccess.PERSONAL


def _authorize_draft_workflow_access(
    workflow: Workflow,
    context: RequestContext,
    *,
    workspace_id: str | None = None,
) -> None:
    """Authorize access to an unpublished workflow draft."""
    workflow_workspaces = set(_extract_workflow_workspace_ids(workflow))
    if workflow.workspace_id and not workflow_workspaces:
        workflow_workspaces.add(_normalize_workspace_id(str(workflow.workspace_id)))
    request_workspaces = frozenset(
        _normalize_workspace_id(workspace_id)
        for workspace_id in context.workspace_ids
        if workspace_id
    )
    if workspace_id:  # pragma: no branch
        request_workspaces = request_workspaces | {
            _normalize_workspace_id(workspace_id)
        }
    if workflow.draft_access is WorkflowDraftAccess.AUTHENTICATED:
        return
    if workflow.draft_access is WorkflowDraftAccess.WORKSPACE:
        if not workflow_workspaces:
            raise AuthorizationError(
                "Workspace access denied for workflow.",
                code="auth.workspace_forbidden",
            )
        if not request_workspaces:
            raise AuthorizationError(  # pragma: no cover - defensive check
                "Workspace access required for workflow.",
                code="auth.workspace_forbidden",
            )
        if not workflow_workspaces.intersection(request_workspaces):
            raise AuthorizationError(
                "Workspace access denied for workflow.",
                code="auth.workspace_forbidden",
            )
        return

    owner = _resolve_workflow_owner(workflow)
    if owner is not None and owner != context.subject:
        if context.identity_type == "developer":
            logger.debug(
                "Bypassing workflow owner check for developer context",
                extra={
                    "workflow_id": str(workflow.id),
                    "owner": owner,
                    "subject": context.subject,
                },
            )
            return
        raise AuthorizationError(
            "Workflow access denied for caller.",
            code="auth.forbidden",
        )


def _resolve_workflow_owner(workflow: Workflow) -> str | None:
    """Return the actor associated with the workflow's creation event."""
    if not workflow.audit_log:
        return None
    return workflow.audit_log[0].actor


def _resolve_authenticated_context(
    policy: AuthorizationPolicy | object,
) -> RequestContext | None:
    """Return authenticated context when auth enforcement is enabled."""
    if not isinstance(policy, AuthorizationPolicy):
        return None
    if not load_auth_settings().enforce:
        return None
    return policy.require_authenticated()


def _resolve_actor(request_actor: str, context: RequestContext | None) -> str:
    """Prefer authenticated subject over client-provided actor."""
    if context is None:
        return request_actor
    return context.subject


def _append_workspace_tags(
    tags: list[str] | None,
    context: RequestContext | None,
    *,
    preserve_none: bool = False,
) -> list[str] | None:
    """Append workspace tags derived from auth context claims."""
    if context is None or not context.workspace_ids:
        return tags
    if tags is None:
        if preserve_none:
            return None
        return [
            f"workspace:{_normalize_workspace_id(workspace_id)}"
            for workspace_id in sorted(context.workspace_ids)
        ]

    merged = [tag.strip() for tag in tags if tag and tag.strip()]
    existing = {tag.lower() for tag in merged}
    for workspace_id in sorted(context.workspace_ids):
        workspace_tag = f"workspace:{_normalize_workspace_id(workspace_id)}"
        if workspace_tag not in existing:
            merged.append(workspace_tag)
            existing.add(workspace_tag)
    return merged


@router.post(
    "/workflows/{workflow_ref}/chatkit/session",
    response_model=ChatKitSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def create_workflow_chatkit_session(
    workflow_ref: str,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
    policy: AuthorizationPolicy = Depends(get_authorization_policy),  # noqa: B008
    issuer: ChatKitSessionTokenIssuer = Depends(resolve_chatkit_token_issuer),  # noqa: B008
) -> ChatKitSessionResponse:
    """Issue a ChatKit JWT scoped to the workflow for authenticated Studio users."""
    tid = str(workspace.workspace_id)
    auth_enforced = load_auth_settings().enforce
    context = policy.context
    if auth_enforced:
        context = policy.require_authenticated()
        policy.require_scopes("workflows:read", "workflows:execute")

    workflow = await _load_workflow_for_request(
        repository,
        workflow_ref,
        workspace_id=tid,
    )
    if workflow.is_archived:
        raise_not_found("Workflow not found", WorkflowNotFoundError(str(workflow.id)))

    if auth_enforced:
        _authorize_draft_workflow_access(
            workflow,
            context,
            workspace_id=str(workspace.workspace_id),
        )

    metadata = {
        "workflow_id": str(workflow.id),
        "workflow_name": workflow.name,
        "source": "studio",
    }
    normalized_workspace_ids = frozenset(
        _normalize_workspace_id(workspace_id)
        for workspace_id in context.workspace_ids
        if workspace_id
    ) | {_normalize_workspace_id(str(workspace.workspace_id))}
    primary_workspace = _normalize_workspace_id(str(workspace.workspace_id))
    token, expires_at = issuer.mint_session(
        subject=context.subject,
        identity_type=context.identity_type,
        token_id=context.token_id,
        workspace_ids=normalized_workspace_ids,
        primary_workspace_id=primary_workspace,
        workflow_id=workflow.id,
        scopes=context.scopes,
        metadata=metadata,
        user=None,
        assistant=None,
        extra={"interface": "studio_chat_bubble"},
    )

    logger.info(
        "Issued workflow ChatKit session token",
        extra={
            "workflow_id": str(workflow.id),
            "subject": context.subject,
            "workspace_id": primary_workspace or "<multiple>",
        },
    )
    return ChatKitSessionResponse(client_secret=token, expires_at=expires_at)


__all__ = ["public_router", "router"]
