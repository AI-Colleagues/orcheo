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
from starlette.concurrency import run_in_threadpool
from orcheo.hosted_apps import (
    AppAuthError,
    AppBinding,
    AppRuntimeConflictError,
    AppRuntimeError,
    AppRuntimeLimitError,
    canonical_app_host,
)
from orcheo.hosted_apps.config import HostedAppsSettings, HostedAppsSettingsError
from orcheo.hosted_apps.errors import AliasValidationError, HostedAppsDisabledError
from orcheo.hosted_apps.models import AppSession
from orcheo.workspace import WorkspaceMembershipError
from orcheo_backend.app.dependencies import get_repository
from orcheo_backend.app.hosted_apps.auth_store import get_app_auth_service
from orcheo_backend.app.hosted_apps.runtime_store import get_app_runtime_service
from orcheo_backend.app.hosted_apps.store import get_hosted_apps_repository
from orcheo_backend.app.workflow_execution import execute_workflow_recorded
from orcheo_backend.app.workspace.dependencies import get_workspace_repository


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
    anonymous_visitor_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class AuthExchangeRequest(BaseModel):
    """Trusted gateway exchange of one PKCE authorization code."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=256)
    verifier: str = Field(min_length=43, max_length=128)
    redirect_uri: str = Field(min_length=1, max_length=2048)


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


async def _resolve_app_session(
    secret: str | None,
    *,
    host: str,
    descriptor: dict[str, Any],
    repository: Any,
) -> AppSession | None:
    """Introspect an exact-host session and recheck current membership."""
    if secret is None:
        return None
    service = get_app_auth_service(repository)
    session = await run_in_threadpool(
        service.introspect,
        secret,
        app_host=host,
        runtime_generation=int(descriptor["generation"]),
        current_member=True,
    )
    if session.workspace_id != UUID(
        descriptor["workspace_id"]
    ) or session.app_id != UUID(descriptor["app_id"]):
        await run_in_threadpool(service.revoke, secret)
        raise AppAuthError("App session is outside the resolved app scope.")
    try:
        await run_in_threadpool(
            get_workspace_repository().get_membership,
            session.workspace_id,
            session.user_id,
        )
    except WorkspaceMembershipError as exc:
        await run_in_threadpool(service.revoke, secret)
        raise AppAuthError("App session membership is no longer active.") from exc
    return session


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
        final_state = await asyncio.wait_for(
            execute_workflow_recorded(
                binding.workflow_id,
                version.graph,
                payload,
                str(execution_id),
                workspace_id=str(binding.workspace_id),
                stored_runnable_config=binding.runnable_config_snapshot,
            ),
            timeout=binding.limits.get("timeout_seconds", 3600),
        )
        await run_in_threadpool(
            runtime.complete, handle, output={"final_state": final_state}
        )
    except Exception as exc:
        logger.exception("Local Hosted App workflow execution failed")
        await run_in_threadpool(runtime.complete, handle, error=str(exc))


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
        descriptor = await run_in_threadpool(_resolve_descriptor, host, repository)
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
    session_secret: Annotated[str | None, Header(alias="X-Orcheo-App-Session")] = None,
) -> dict[str, str]:
    """Accept one immutable release binding without browser-selected ids."""
    try:
        descriptor = await run_in_threadpool(_resolve_descriptor, body.host, repository)
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
        session = await _resolve_app_session(
            session_secret,
            host=body.host,
            descriptor=descriptor,
            repository=repository,
        )
        execution_id = uuid4()
        result = await run_in_threadpool(
            get_app_runtime_service(repository).accept,
            binding,
            workspace_id=binding.workspace_id,
            app_id=binding.app_id,
            release_id=UUID(descriptor["release_id"]),
            deployment_id=UUID(descriptor["deployment_id"]),
            binding_snapshot_sha256=descriptor["snapshot_sha256"],
            payload=body.payload,
            idempotency_key=body.idempotency_key,
            runtime_generation=int(descriptor["generation"]),
            visitor_user_id=session.user_id if session else None,
            session_id=session.id if session else None,
            anonymous_visitor_id=body.anonymous_visitor_id,
            workflow_run_id=execution_id,
            client_ip=body.client_ip,
        )
        if result.newly_accepted:
            _schedule_local_runtime_run(
                handle=result.handle,
                binding=binding,
                payload=body.payload,
                execution_id=execution_id,
            )
    except AppRuntimeLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    except AppRuntimeConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except AppAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
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
    session_secret: Annotated[str | None, Header(alias="X-Orcheo-App-Session")] = None,
) -> dict[str, Any]:
    """Return visitor-safe status for an opaque bearer handle."""
    try:
        descriptor = await run_in_threadpool(_resolve_descriptor, host, repository)
        session = await _resolve_app_session(
            session_secret,
            host=host,
            descriptor=descriptor,
            repository=repository,
        )
        result = await run_in_threadpool(
            get_app_runtime_service(repository).status,
            handle,
            workspace_id=UUID(descriptor["workspace_id"]),
            app_id=UUID(descriptor["app_id"]),
            runtime_generation=int(descriptor["generation"]),
            visitor_user_id=session.user_id if session else None,
            session_id=session.id if session else None,
        )
    except AppAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
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


@router.post("/auth/exchange", dependencies=[Depends(authenticate_app_gateway)])
async def exchange_app_code(
    body: AuthExchangeRequest,
    repository: Annotated[Any, Depends(get_hosted_apps_repository)],
) -> dict[str, str]:
    """Exchange a central code for an exact-host HttpOnly cookie secret."""
    try:
        descriptor = await run_in_threadpool(_resolve_descriptor, body.host, repository)
        service = get_app_auth_service(repository)
        issued = await run_in_threadpool(
            service.exchange,
            raw_code=body.code,
            verifier=body.verifier,
            app_host=body.host,
            redirect_uri=body.redirect_uri,
            runtime_generation=int(descriptor["generation"]),
            current_member=True,
        )
        if issued.session.workspace_id != UUID(
            descriptor["workspace_id"]
        ) or issued.session.app_id != UUID(descriptor["app_id"]):
            await run_in_threadpool(service.revoke, issued.secret)
            raise AppAuthError("App authorization code is outside the app scope.")
        try:
            await run_in_threadpool(
                get_workspace_repository().get_membership,
                issued.session.workspace_id,
                issued.session.user_id,
            )
        except WorkspaceMembershipError:
            await run_in_threadpool(service.revoke, issued.secret)
            raise
    except (AppAuthError, WorkspaceMembershipError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="App authorization failed.",
        ) from exc
    return {"session_secret": issued.secret}


@router.get("/auth/session", dependencies=[Depends(authenticate_app_gateway)])
async def inspect_app_session(
    host: Annotated[str, Query(min_length=1, max_length=255)],
    repository: Annotated[Any, Depends(get_hosted_apps_repository)],
    session_secret: Annotated[str | None, Header(alias="X-Orcheo-App-Session")] = None,
) -> dict[str, bool]:
    """Return only whether the current exact-host session is authenticated."""
    try:
        descriptor = await run_in_threadpool(_resolve_descriptor, host, repository)
        session = await _resolve_app_session(
            session_secret,
            host=host,
            descriptor=descriptor,
            repository=repository,
        )
    except (AppAuthError, KeyError, ValueError):
        session = None
    return {"authenticated": session is not None}
