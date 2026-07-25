"""Platform-scoped Hosted Apps moderation outside workspace authority."""

from __future__ import annotations
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from orcheo.hosted_apps import InMemoryHostedAppsRepository
from orcheo_backend.app.authentication import (
    RequestContext,
    authenticate_request,
    get_request_context,
    require_scopes,
)
from orcheo_backend.app.hosted_apps import get_hosted_apps_repository


MODERATION_SCOPE = "platform:hosted-apps:moderate"
OPERATE_SCOPE = "platform:hosted-apps:runtime-control"
router = APIRouter(
    prefix="/platform/hosted-apps",
    tags=["platform-hosted-apps"],
    dependencies=[
        Depends(authenticate_request),
        Depends(require_scopes(MODERATION_SCOPE)),
    ],
)
RepositoryDep = Annotated[
    InMemoryHostedAppsRepository, Depends(get_hosted_apps_repository)
]
operations_router = APIRouter(
    prefix="/platform/hosted-apps",
    tags=["platform-hosted-apps"],
    dependencies=[
        Depends(authenticate_request),
        Depends(require_scopes(OPERATE_SCOPE)),
    ],
)


class ModerationBlockRequest(BaseModel):
    """Reasoned platform override."""

    model_config = ConfigDict(extra="forbid")

    target_kind: str = Field(pattern="^(app|alias|workspace|publisher)$")
    target_id: str = Field(min_length=1, max_length=255)
    reason_code: str = Field(min_length=1, max_length=100)
    reason_detail: str | None = Field(default=None, max_length=4000)


class PlatformAliasRequest(BaseModel):
    """Platform alias reservation."""

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=3, max_length=48)


class RuntimeControlRequest(BaseModel):
    """Global cross-plane runtime control."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


@router.post("/blocks", status_code=status.HTTP_201_CREATED)
async def create_block(
    body: ModerationBlockRequest,
    repository: RepositoryDep,
    auth: Annotated[RequestContext, Depends(get_request_context)],
) -> dict:
    """Create an immediately effective platform block."""
    block = repository.create_moderation_block(**body.model_dump(), actor=auth.subject)
    return block.model_dump(mode="json")


@router.post("/blocks/{block_id}/reinstate")
async def reinstate_block(
    block_id: UUID,
    repository: RepositoryDep,
    auth: Annotated[RequestContext, Depends(get_request_context)],
) -> dict:
    """Lift one exact block without changing workspace-controlled state."""
    try:
        block = repository.lift_moderation_block(block_id, actor=auth.subject)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Moderation block was not found.",
        ) from exc
    return block.model_dump(mode="json")


@router.post("/reserved-aliases", status_code=status.HTTP_201_CREATED)
async def reserve_alias(
    body: PlatformAliasRequest,
    repository: RepositoryDep,
    auth: Annotated[RequestContext, Depends(get_request_context)],
) -> dict:
    """Reserve an alias globally for platform use."""
    try:
        alias = repository.reserve_platform_alias(body.alias, actor=auth.subject)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return alias.model_dump(mode="json")


@router.get("/aliases/{alias}/owner")
async def lookup_alias_owner(alias: str, repository: RepositoryDep) -> dict:
    """Resolve alias ownership for authorized abuse and incident operators."""
    try:
        owner = repository.lookup_alias_owner(alias)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alias was not found."
        ) from exc
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alias was not found."
        )
    return owner


@operations_router.put("/runtime")
async def update_runtime(
    body: RuntimeControlRequest,
    repository: RepositoryDep,
    auth: Annotated[RequestContext, Depends(get_request_context)],
) -> dict:
    """Increment the global generation on every enablement transition."""
    state = repository.set_runtime_enabled(enabled=body.enabled, actor=auth.subject)
    return state.model_dump(mode="json")


@operations_router.get("/runtime")
async def get_runtime(repository: RepositoryDep) -> dict:
    """Inspect the durable runtime generation without selected-workspace state."""
    return repository.get_runtime_generation().model_dump(mode="json")
