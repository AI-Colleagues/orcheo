"""Gateway-only Hosted Apps backend routes outside workspace-selected APIs."""

from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import logging
import os
from typing import Annotated, Any
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from orcheo.hosted_apps import AppBinding, AppRuntimeError, canonical_app_host
from orcheo.hosted_apps.config import HostedAppsSettings, HostedAppsSettingsError
from orcheo.hosted_apps.errors import AliasValidationError, HostedAppsDisabledError
from orcheo_backend.app.dependencies import get_repository
from orcheo_backend.app.hosted_apps.runtime_store import get_app_runtime_service
from orcheo_backend.app.hosted_apps.store import get_hosted_apps_repository
from orcheo_backend.app.workflow_execution import execute_workflow_recorded


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/internal/hosted-apps",
    include_in_schema=False,
    tags=["internal-hosted-apps"],
)
_local_runtime_tasks: set[asyncio.Task[None]] = set()


class RuntimeAcceptRequest(BaseModel):
    """Trusted normalized invocation from the app gateway."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    binding: str = Field(min_length=1, max_length=63)
    payload: Any
    idempotency_key: str = Field(min_length=1, max_length=256)
    client_ip: str


async def authenticate_app_gateway(
    request: Request,
    token: Annotated[str | None, Header(alias="X-Orcheo-App-Gateway-Token")] = None,
) -> None:
    """Accept only the dedicated gateway secret and reject browser/API identity."""
    expected = os.getenv("ORCHEO_APP_GATEWAY_SECRET", "")
    forbidden = {
        "authorization",
        "x-orcheo-workspace",
        "x-orcheo-actor",
        "x-orcheo-service-token",
    }
    if (
        not expected
        or token is None
        or not hmac.compare_digest(token, expected)
        or any(name in request.headers for name in forbidden)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="App gateway authentication failed.",
        )


def _resolve_descriptor(host: str, repository: Any) -> dict[str, Any]:
    """Resolve without a caller-selected workspace or resource identifier."""
    settings = HostedAppsSettings.from_environment()
    if not settings.enabled or settings.base_domain is None:
        raise HostedAppsDisabledError("Hosted Apps runtime is disabled.")
    _canonical, alias = canonical_app_host(host, settings.base_domain)
    return repository.resolve_descriptor(alias)


async def _execute_local_runtime_run(
    *,
    handle: str,
    binding: AppBinding,
    payload: Any,
    execution_id: UUID,
) -> None:
    """Execute an accepted Hosted App run in local/single-node deployments."""
    runtime = get_app_runtime_service()
    try:
        repository = get_repository()
        version = await repository.get_version(binding.workflow_version_id)
        if version.workflow_id != binding.workflow_id or version.workspace_id != str(
            binding.workspace_id
        ):
            raise ValueError("Bound workflow version is outside the app workspace.")
        executable = {
            "graph_sha256": version.compute_checksum(),
            "runnable_config": binding.runnable_config_snapshot,
        }
        digest = hashlib.sha256(
            json.dumps(executable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if not hmac.compare_digest(digest, binding.workflow_execution_sha256):
            raise ValueError("Bound workflow executable evidence is stale.")

        if not isinstance(payload, dict):
            raise ValueError("Workflow input must be a JSON object.")
        final_state = await execute_workflow_recorded(
            binding.workflow_id,
            version.graph,
            payload,
            str(execution_id),
            workspace_id=str(binding.workspace_id),
            stored_runnable_config=binding.runnable_config_snapshot,
        )
        runtime.complete(handle, output={"final_state": final_state})
    except Exception as exc:
        logger.exception("Local Hosted App workflow execution failed")
        runtime.complete(handle, error=str(exc))


def _schedule_local_runtime_run(
    *,
    handle: str,
    binding: AppBinding,
    payload: Any,
    execution_id: UUID,
) -> None:
    """Schedule inline execution only for supported local deployment modes."""
    if os.getenv("ORCHEO_DEPLOYMENT_MODE", "").strip().lower() not in {
        "local",
        "single-node",
    }:
        return
    task = asyncio.create_task(
        _execute_local_runtime_run(
            handle=handle,
            binding=binding,
            payload=payload,
            execution_id=execution_id,
        )
    )
    _local_runtime_tasks.add(task)
    task.add_done_callback(_local_runtime_tasks.discard)


@router.get("/resolve", dependencies=[Depends(authenticate_app_gateway)])
async def resolve_host(
    host: Annotated[str, Query(min_length=1, max_length=255)],
    repository: Annotated[Any, Depends(get_hosted_apps_repository)],
) -> dict[str, Any]:
    """Resolve an exact app host to an immutable release descriptor."""
    try:
        settings = HostedAppsSettings.from_environment()
        if settings.base_domain is None:
            raise HostedAppsDisabledError("Hosted Apps runtime is disabled.")
        canonical_host, _alias = canonical_app_host(host, settings.base_domain)
        descriptor = _resolve_descriptor(host, repository)
    except (HostedAppsSettingsError, HostedAppsDisabledError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hosted Apps runtime is unavailable.",
        ) from None
    except (AliasValidationError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hosted app was not found.",
        ) from None
    return {"host": canonical_host, **descriptor}


@router.post("/runtime/runs", dependencies=[Depends(authenticate_app_gateway)])
async def accept_runtime_run(
    body: RuntimeAcceptRequest,
    repository: Annotated[Any, Depends(get_hosted_apps_repository)],
) -> dict[str, str]:
    """Accept one immutable release binding without browser-selected ids."""
    try:
        descriptor = _resolve_descriptor(body.host, repository)
        snapshots = descriptor.get("capability_snapshot", {}).get("bindings", [])
        snapshot = next(
            item
            for item in snapshots
            if isinstance(item, dict) and item.get("name") == body.binding
        )
        binding = AppBinding(
            **snapshot,
            workspace_id=UUID(descriptor["workspace_id"]),
            app_id=UUID(descriptor["app_id"]),
        )
        execution_id = uuid4()
        result = get_app_runtime_service().accept(
            binding,
            workspace_id=binding.workspace_id,
            app_id=binding.app_id,
            release_id=UUID(descriptor["release_id"]),
            deployment_id=UUID(descriptor["deployment_id"]),
            binding_snapshot_sha256=descriptor["snapshot_sha256"],
            payload=body.payload,
            idempotency_key=body.idempotency_key,
            runtime_generation=int(descriptor["generation"]),
            visitor_user_id=None,
            session_id=None,
            workflow_run_id=execution_id,
        )
        if result.newly_accepted:
            _schedule_local_runtime_run(
                handle=result.handle,
                binding=binding,
                payload=body.payload,
                execution_id=execution_id,
            )
    except (StopIteration, KeyError, ValueError, AppRuntimeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow binding is unavailable.",
        ) from None
    return {"handle": result.handle, "status": result.status}


@router.get("/runtime/runs/{handle}", dependencies=[Depends(authenticate_app_gateway)])
async def get_runtime_run(
    handle: str,
    host: Annotated[str, Query(min_length=1, max_length=255)],
    repository: Annotated[Any, Depends(get_hosted_apps_repository)],
) -> dict[str, Any]:
    """Return visitor-safe status for an opaque bearer handle."""
    try:
        descriptor = _resolve_descriptor(host, repository)
        result = get_app_runtime_service().status(
            handle,
            workspace_id=UUID(descriptor["workspace_id"]),
            app_id=UUID(descriptor["app_id"]),
            runtime_generation=int(descriptor["generation"]),
            visitor_user_id=None,
            session_id=None,
        )
    except (KeyError, ValueError, AppRuntimeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow run is unavailable.",
        ) from None
    return {
        "handle": result.handle,
        "status": result.status,
        "output": result.output,
        "error": result.error,
    }
