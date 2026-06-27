"""Endpoints for candidate AI colleagues from the candidates repo."""

from __future__ import annotations
import logging
from typing import Any, NoReturn
from uuid import UUID
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ValidationError
from orcheo.graph.ingestion import (
    ScriptIngestionError,
    ingest_langgraph_script,
    load_graph_from_script_full_env,
)
from orcheo.models import Workflow, WorkflowDraftAccess
from orcheo.runtime.configurable_schema import (
    ConfigurableSchemaError,
    split_configurable,
)
from orcheo.runtime.runnable_config import RunnableConfigModel
from orcheo.workflow.mermaid import render_mermaid_from_graph_payload_full_env
from orcheo_backend.app.candidates_service import (
    CandidateFetchError,
    get_candidate_source_ref,
    get_candidates,
)
from orcheo_backend.app.dependencies import RepositoryDep
from orcheo_backend.app.errors import WorkspaceQuotaExceededError
from orcheo_backend.app.plugin_inventory import (
    missing_required_plugins,
    required_plugins_from_metadata,
)
from orcheo_backend.app.repository import (
    WorkflowHandleConflictError,
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository.errors import TeamNotFoundError
from orcheo_backend.app.schemas.candidates import CandidateItem, CandidatePublicItem
from orcheo_backend.app.teams_service import ensure_default_team
from orcheo_backend.app.workspace import WorkspaceContextDep
from orcheo_backend.app.workspace_governance import ensure_workspace_workflow_quota
from orcheo_sdk.cli.errors import CLIError
from orcheo_sdk.cli.workflow.frontmatter import compare_semver, parse_semver


logger = logging.getLogger(__name__)
router = APIRouter()


class CandidateOnboardRequest(BaseModel):
    """Request body for server-side candidate onboarding."""

    id: str
    team_id: str | None = None


class CandidateUpdateRequest(BaseModel):
    """Request body for updating a workflow from a candidate release."""

    workflow_id: str
    candidate_id: str


def _candidates_502(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Unable to load candidate colleagues from the repository.",
    )


async def _fetch_candidate_by_id(candidate_id: str) -> CandidateItem:
    """Return the candidate with *candidate_id* from the server-side cache."""
    try:
        candidates = await get_candidates()
    except CandidateFetchError as exc:
        raise _candidates_502(exc) from exc

    candidate = next((c for c in candidates if c.id == candidate_id), None)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate '{candidate_id}' not found.",
        )
    return candidate


def _build_version_metadata(candidate: CandidateItem) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        **(candidate.metadata or {}),
        "source": "candidate-onboard",
        "candidate_id": candidate.id,
        "candidate_handle": candidate.handle,
        "candidate_version": candidate.version,
        "candidate_source_ref": get_candidate_source_ref(),
    }
    if candidate.avatar:
        metadata.setdefault("avatar", candidate.avatar)
    if candidate.subtitle:
        metadata.setdefault("subtitle", candidate.subtitle)
    return metadata


def _raise_candidate_error(
    status_code: int,
    message: str,
    code: str,
) -> NoReturn:
    """Raise a structured candidate HTTP error."""
    raise HTTPException(
        status_code=status_code,
        detail={"message": message, "code": code},
    )


def _validate_candidate_version(candidate: CandidateItem) -> str:
    """Return the candidate SemVer or reject unversioned/malformed candidates."""
    if candidate.version is None:
        _raise_candidate_error(
            status.HTTP_400_BAD_REQUEST,
            "Candidate does not declare a release version.",
            "candidate.unversioned",
        )
    try:
        parse_semver(candidate.version)
    except CLIError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Candidate release version is invalid.",
                "code": "candidate.invalid_version",
            },
        ) from exc
    return candidate.version


def _prepare_candidate_version_payload(
    candidate: CandidateItem,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Validate and build the version payload for a candidate release."""
    metadata = _build_version_metadata(candidate)
    _raise_for_missing_required_plugins(metadata)
    runnable_config, metadata = _resolve_candidate_runnable_config(candidate, metadata)

    try:
        graph_payload = ingest_langgraph_script(
            candidate.script,
            entrypoint=candidate.entrypoint,
        )
    except ScriptIngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": f"Candidate script ingestion failed: {exc}",
                "code": "candidate.script_ingestion_failed",
            },
        ) from exc

    # ``ingest_langgraph_script`` only compiles the source (a syntax check); it
    # never executes it, so import errors and other runtime build failures slip
    # through.  Build the graph in the full environment here so a broken
    # candidate fails onboarding loudly instead of silently producing a workflow
    # that only errors at execution time.
    try:
        load_graph_from_script_full_env(
            candidate.script,
            entrypoint=candidate.entrypoint,
        )
    except ScriptIngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": f"Candidate script failed to build: {exc}",
                "code": "candidate.script_build_failed",
            },
        ) from exc

    mermaid = render_mermaid_from_graph_payload_full_env(graph_payload)
    if mermaid and isinstance(graph_payload.get("index"), dict):
        graph_payload["index"]["mermaid"] = mermaid

    return graph_payload, metadata, runnable_config


async def _create_candidate_version_from_payload(
    repository: RepositoryDep,
    workflow: Workflow,
    candidate: CandidateItem,
    payload: tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None],
    *,
    actor: str,
) -> None:
    """Persist a prepared candidate workflow version."""
    graph_payload, metadata, runnable_config = payload
    await repository.create_version(
        workflow.id,
        graph=graph_payload,
        metadata=metadata,
        notes=candidate.notes,
        created_by=actor,
        runnable_config=runnable_config,
    )


async def _append_candidate_workflow_version(
    repository: RepositoryDep,
    workflow: Workflow,
    candidate: CandidateItem,
    *,
    actor: str,
) -> None:
    """Append a workflow version sourced from the candidate release."""
    payload = _prepare_candidate_version_payload(candidate)
    await _create_candidate_version_from_payload(
        repository,
        workflow,
        candidate,
        payload,
        actor=actor,
    )


def _workflow_matches_candidate_source(
    metadata: dict[str, Any],
    candidate: CandidateItem,
) -> bool:
    """Return True when version metadata identifies the same source candidate."""
    if metadata.get("source") != "candidate-onboard":
        return False
    candidate_id = metadata.get("candidate_id")
    candidate_handle = metadata.get("candidate_handle")
    # Fall back to handles for workflow versions created before candidate_id metadata.
    return candidate_id == candidate.id or candidate_handle == candidate.handle


def _merge_configurable_schema(
    existing: Any,
    inline: dict[str, Any],
) -> dict[str, Any]:
    """Merge inline schema declarations with authored schema metadata.

    Candidate metadata can include a ``configurable_schema`` map authored from
    frontmatter or a sibling schema file. Preserve those explicit definitions
    and only fill in fields discovered from inline config annotations.
    """
    if isinstance(existing, dict):
        return {**inline, **existing}
    return inline


def _resolve_candidate_runnable_config(
    candidate: CandidateItem,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Normalize candidate config.json before version creation."""
    if candidate.config is None:
        return None, metadata

    try:
        runnable_config = RunnableConfigModel.model_validate(candidate.config)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Candidate config.json is not a valid runnable config.",
        ) from exc

    serialized_config = runnable_config.model_dump(
        mode="json",
        exclude_defaults=True,
        exclude_none=True,
    )
    if not runnable_config.configurable:
        return serialized_config, metadata

    try:
        resolved_configurable, inline_schema = split_configurable(
            runnable_config.configurable
        )
    except ConfigurableSchemaError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if inline_schema:
        merged_schema = _merge_configurable_schema(
            metadata.get("configurable_schema"), inline_schema
        )
        metadata = {
            **metadata,
            "configurable_schema": merged_schema,
            # Persist the authored field order separately: ``configurable_schema``
            # is stored in a JSONB column that does not preserve object key order.
            "configurable_schema_order": list(merged_schema.keys()),
        }
        runnable_config = runnable_config.model_copy(
            update={"configurable": resolved_configurable}
        )

    return (
        runnable_config.model_dump(
            mode="json",
            exclude_defaults=True,
            exclude_none=True,
        ),
        metadata,
    )


def _raise_for_missing_required_plugins(metadata: dict[str, Any]) -> None:
    """Reject candidate onboarding when declared plugins are unavailable."""
    required_plugins = required_plugins_from_metadata(metadata)
    missing_plugins = missing_required_plugins(required_plugins)
    if not missing_plugins:
        return

    plugin_list = ", ".join(missing_plugins)
    noun = "plugin" if len(missing_plugins) == 1 else "plugins"

    # Provide more helpful installation guidance
    install_commands = []
    for plugin in missing_plugins:
        # Common convention: orcheo-plugin-{name}
        install_commands.append(f"pip install orcheo-plugin-{plugin}")

    install_help = " ".join(install_commands)

    logger.warning(
        "Candidate onboarding failed: missing required plugins",
        extra={
            "missing_plugins": missing_plugins,
            "required_plugins": list(required_plugins),
            "installation_help": install_help,
        },
    )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "message": f"Missing required {noun} for this candidate: {plugin_list}",
            "code": "candidate.missing_plugins",
            "missing_plugins": missing_plugins,
            "installation_help": f"Try: {install_help}",
        },
    )


@router.get("/candidates", response_model=list[CandidatePublicItem])
async def list_candidates() -> list[CandidateItem]:
    """Return candidate AI colleagues sourced from the candidates repository.

    Script, entrypoint, and config are stripped from the response; ingestion
    is performed server-side via ``POST /candidates/onboard``.
    """
    try:
        return await get_candidates()
    except CandidateFetchError as exc:
        raise _candidates_502(exc) from exc


@router.post(
    "/candidates/onboard",
    response_model=Workflow,
    status_code=status.HTTP_200_OK,
)
async def onboard_candidate(
    request: CandidateOnboardRequest,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> Workflow:
    """Onboard an official candidate AI colleague into the workspace.

    The workflow script is sourced exclusively from the server-side candidates
    cache — any script in the request body is ignored.

    - If the candidate's handle does not exist in the workspace, a new workflow
      is created and version 1 is ingested.
    - If the handle already exists, a new version is appended (re-onboard /
      upstream-revision upgrade path).

    Returns the workflow record (new or existing).
    """
    candidate = await _fetch_candidate_by_id(request.id)

    logger.info(
        "Starting candidate onboarding",
        extra={
            "candidate_id": request.id,
            "candidate_handle": candidate.handle,
            "candidate_name": candidate.name,
            "workspace_id": str(workspace.workspace_id),
        },
    )

    version_payload = _prepare_candidate_version_payload(candidate)
    workspace_id = str(workspace.workspace_id)

    # Resolve the target team. When unspecified, onboard into the default team.
    default_team = await ensure_default_team(repository, workspace)
    target_team_id = request.team_id or str(default_team.id)
    if request.team_id is not None:
        try:
            await repository.get_team(UUID(request.team_id), workspace_id=workspace_id)
        except (TeamNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "message": f"Team '{request.team_id}' not found.",
                    "code": "team.not_found",
                },
            ) from exc

    # If the candidate already lives in the target team, append a version;
    # otherwise onboard it as a new colleague within that team.
    workflow: Workflow
    try:
        workflow_id = await repository.resolve_workflow_ref(
            candidate.handle,
            include_archived=False,
            workspace_id=workspace_id,
            team_id=target_team_id,
        )
        workflow = await repository.get_workflow(workflow_id, workspace_id=workspace_id)
    except WorkflowNotFoundError:
        try:
            await ensure_workspace_workflow_quota(repository, workspace)
        except WorkspaceQuotaExceededError as exc:
            raise exc.as_http_exception() from exc
        try:
            workflow = await repository.create_workflow(
                name=candidate.name,
                handle=candidate.handle,
                slug=None,
                description=candidate.description,
                tags=["langgraph"],
                draft_access=WorkflowDraftAccess.WORKSPACE,
                actor="onboard",
                workspace_id=workspace_id,
                team_id=target_team_id,
            )
        except WorkflowHandleConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": str(exc), "code": "workflow.handle.conflict"},
            ) from exc

    await _create_candidate_version_from_payload(
        repository,
        workflow,
        candidate,
        version_payload,
        actor="onboard",
    )

    logger.info(
        "Candidate onboarding completed successfully",
        extra={
            "candidate_id": request.id,
            "candidate_handle": candidate.handle,
            "workflow_id": workflow.id,
            "workspace_id": str(workspace.workspace_id),
        },
    )

    return workflow


@router.post(
    "/candidates/update",
    response_model=Workflow,
    status_code=status.HTTP_200_OK,
)
async def update_candidate_workflow(
    request: CandidateUpdateRequest,
    repository: RepositoryDep,
    workspace: WorkspaceContextDep,
) -> Workflow:
    """Update an existing onboarded colleague from the latest candidate release."""
    candidate = await _fetch_candidate_by_id(request.candidate_id)
    latest_candidate_version = _validate_candidate_version(candidate)

    workspace_id = str(workspace.workspace_id)
    try:
        workflow_id = UUID(request.workflow_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": f"Workflow '{request.workflow_id}' not found.",
                "code": "workflow.not_found",
            },
        ) from exc

    try:
        workflow = await repository.get_workflow(
            workflow_id,
            workspace_id=workspace_id,
        )
        latest_version = await repository.get_latest_version(workflow.id)
    except WorkflowNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Workflow not found.", "code": "workflow.not_found"},
        ) from exc
    except WorkflowVersionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Workflow has no installed candidate version.",
                "code": "candidate.workflow_unversioned",
            },
        ) from exc

    metadata = latest_version.metadata
    if not _workflow_matches_candidate_source(metadata, candidate):
        _raise_candidate_error(
            status.HTTP_409_CONFLICT,
            "Workflow is not sourced from the requested candidate.",
            "candidate.source_mismatch",
        )

    installed_version = metadata.get("candidate_version")
    if not isinstance(installed_version, str):
        _raise_candidate_error(
            status.HTTP_409_CONFLICT,
            "Workflow does not have an installed candidate version.",
            "candidate.installed_version_unknown",
        )

    try:
        comparison = compare_semver(latest_candidate_version, installed_version)
    except CLIError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Workflow installed candidate version is invalid.",
                "code": "candidate.installed_version_invalid",
            },
        ) from exc

    # Block equal or older candidate releases.
    if comparison <= 0:
        _raise_candidate_error(
            status.HTTP_409_CONFLICT,
            "Candidate is already current for this workflow.",
            "candidate.no_update_available",
        )

    await _append_candidate_workflow_version(
        repository,
        workflow,
        candidate,
        actor="candidate-update",
    )

    logger.info(
        "Candidate workflow updated successfully",
        extra={
            "candidate_id": candidate.id,
            "candidate_handle": candidate.handle,
            "candidate_version": candidate.version,
            "workflow_id": str(workflow.id),
            "workspace_id": str(workspace.workspace_id),
        },
    )

    return workflow
