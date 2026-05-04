"""Application factory for the Orcheo FastAPI service."""

from __future__ import annotations
import json
import os
from contextlib import asynccontextmanager
from typing import Any, cast
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from orcheo.plugins import load_enabled_plugins
from orcheo.tracing import configure_tracing
from orcheo.vault.oauth import OAuthCredentialService
from orcheo_backend.app.authentication import (
    AuthenticationError,
    authenticate_request,
    load_auth_settings,
)
from orcheo_backend.app.chatkit_runtime import (
    cancel_chatkit_cleanup_task,
    ensure_chatkit_cleanup_task,
    get_chatkit_server,
    sensitive_logging_enabled,
)
from orcheo_backend.app.dependencies import (
    get_credential_service,
    get_history_store,
    get_listener_runtime_store,
    get_repository,
    get_vault,
    set_credential_service,
    set_history_store,
    set_listener_runtime_store,
    set_repository,
    set_vault,
)
from orcheo_backend.app.history import RunHistoryStore
from orcheo_backend.app.listener_runtime_service import ListenerRuntimeService
from orcheo_backend.app.logging_config import configure_logging
from orcheo_backend.app.repository import WorkflowRepository
from orcheo_backend.app.routers import (
    agentensor,
    auth,
    chatkit_assets,
    credential_alerts,
    credential_health,
    credential_templates,
    credentials,
    listeners,
    nodes,
    remediations,
    runs,
    system,
    triggers,
    websocket,
    workflows,
)
from orcheo_backend.app.routers import (
    chatkit as chatkit_router,
)
from orcheo_backend.app.service_token_endpoints import router as service_token_router
from orcheo_backend.app.workflow_execution import configure_sensitive_logging


load_dotenv()
configure_logging()
configure_tracing()

configure_sensitive_logging(
    enable_sensitive_debug=sensitive_logging_enabled(),
)


async def _authentication_error_handler(request: Request, exc: Exception) -> Response:
    """Translate AuthenticationError instances into structured HTTP responses."""
    auth_error = cast(AuthenticationError, exc)
    http_exc = auth_error.as_http_exception()
    return await http_exception_handler(request, http_exc)


def _build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    protected_router = APIRouter(dependencies=[Depends(authenticate_request)])
    protected_router.include_router(service_token_router)
    protected_router.include_router(workflows.router)
    protected_router.include_router(credentials.router)
    protected_router.include_router(credential_templates.router)
    protected_router.include_router(credential_alerts.router)
    protected_router.include_router(credential_health.router)
    protected_router.include_router(listeners.router)
    protected_router.include_router(remediations.router)
    protected_router.include_router(runs.router)
    protected_router.include_router(triggers.router)
    protected_router.include_router(nodes.router)
    protected_router.include_router(agentensor.router)
    protected_router.include_router(system.router)

    router.include_router(workflows.public_router)
    router.include_router(chatkit_router.router)
    router.include_router(auth.router)
    router.include_router(system.public_router)
    # Public webhook invocation routes - external services (Slack, GitHub, etc.)
    # cannot provide Orcheo auth tokens. Security is enforced via webhook-level
    # validation (HMAC signatures, shared secrets) configured per workflow.
    router.include_router(triggers.public_webhook_router)
    router.include_router(protected_router)
    return router


api_router = _build_api_router()


_DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def _load_allowed_origins() -> list[str]:
    """Return the list of CORS-allowed origins based on environment configuration."""
    raw = os.getenv("ORCHEO_CORS_ALLOW_ORIGINS")
    if not raw:
        return list(_DEFAULT_ALLOWED_ORIGINS)
    candidates: list[str] = []
    parsed: list[str] | str | None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw

    if isinstance(parsed, str):
        candidates = [entry.strip() for entry in parsed.split(",")]
    elif isinstance(parsed, list):
        candidates = [str(entry).strip() for entry in parsed]

    origins = [origin for origin in candidates if origin]  # pragma: no cover
    return origins or list(_DEFAULT_ALLOWED_ORIGINS)


def create_app(
    repository: WorkflowRepository | None = None,
    *,
    history_store: RunHistoryStore | None = None,
    credential_service: OAuthCredentialService | None = None,
) -> FastAPI:
    """Instantiate and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        """Manage application lifespan with startup and shutdown logic."""
        load_auth_settings(refresh=True)
        load_enabled_plugins(force=True)
        listener_runtime = ListenerRuntimeService(
            repository=get_repository(),
            vault=get_vault(),
            runtime_store=get_listener_runtime_store(),
        )
        app.state.listener_runtime = listener_runtime
        try:
            get_chatkit_server()
            await ensure_chatkit_cleanup_task()
        except Exception:
            pass
        await listener_runtime.start()
        try:
            yield
        finally:
            await listener_runtime.stop()
            await cancel_chatkit_cleanup_task()

    application = FastAPI(lifespan=lifespan)

    @application.get("/robots.txt", include_in_schema=False)
    async def robots_txt() -> PlainTextResponse:
        """Expose crawl policy for public backend deployments."""
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    allowed_origins = _load_allowed_origins()

    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if repository is not None:
        set_repository(repository)  # pragma: no mutate - override for tests
        application.dependency_overrides[get_repository] = lambda: repository
    if history_store is not None:
        set_history_store(history_store)
        application.dependency_overrides[get_history_store] = lambda: history_store
    listener_runtime_store = get_listener_runtime_store()
    set_listener_runtime_store(listener_runtime_store)
    if credential_service is not None:
        set_credential_service(credential_service)
        set_vault(getattr(credential_service, "_vault", None))
        application.dependency_overrides[get_credential_service] = lambda: (
            credential_service
        )
    elif repository is not None:
        inferred_service = getattr(repository, "_credential_service", None)
        if inferred_service is not None:
            set_credential_service(inferred_service)
            application.dependency_overrides[get_credential_service] = lambda: (
                inferred_service
            )

    application.include_router(api_router)
    application.include_router(chatkit_assets.router)
    application.include_router(websocket.router)
    application.state.listener_runtime_store = listener_runtime_store
    application.add_exception_handler(
        AuthenticationError, _authentication_error_handler
    )

    return application


__all__ = ["create_app"]
