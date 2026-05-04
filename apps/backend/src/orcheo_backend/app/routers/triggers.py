"""Workflow trigger routes."""

from __future__ import annotations
import json
import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from langchain_core.runnables import RunnableConfig
from orcheo.config import get_settings
from orcheo.graph.builder import build_graph
from orcheo.models import CredentialAccessContext
from orcheo.models.workflow import WorkflowRun, WorkflowVersion
from orcheo.persistence import create_checkpointer, create_graph_store
from orcheo.runtime.credentials import CredentialResolver, credential_resolution
from orcheo.runtime.runnable_config import merge_runnable_configs
from orcheo.runtime.state_builder import build_initial_state
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.manual import ManualDispatchRequest
from orcheo.triggers.webhook import WebhookTriggerConfig, WebhookValidationError
from orcheo.vault.oauth import CredentialHealthError
from orcheo_backend.app.dependencies import (
    RepositoryDep,
    VaultDep,
    resolve_workflow_ref_id,
)
from orcheo_backend.app.errors import raise_not_found, raise_webhook_error
from orcheo_backend.app.repository import (
    CronTriggerNotFoundError,
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.schemas.runs import CronDispatchRequest
from orcheo_backend.app.tenancy import TenantContextDep


logger = logging.getLogger(__name__)

router = APIRouter()

# Public router for webhook invocation endpoints that external services (Slack, etc.)
# call directly. These routes are NOT protected by authentication because external
# services cannot provide Orcheo auth tokens. Security is enforced via webhook-level
# validation (HMAC signatures, shared secrets, etc.) configured per workflow.
public_webhook_router = APIRouter()


def _parse_webhook_body(
    raw_body: bytes, *, preserve_raw_body: bool
) -> tuple[Any, dict[str, Any] | None]:
    if not raw_body:
        return {}, None

    decoded_body = raw_body.decode("utf-8", errors="replace")
    parsed_body: Any | None = None
    try:
        parsed_body = json.loads(decoded_body)
    except json.JSONDecodeError:
        parsed_body = None

    if preserve_raw_body:
        payload: Any = {"raw": decoded_body}
        if parsed_body is not None:  # pragma: no branch
            payload["parsed"] = parsed_body
        return payload, parsed_body if isinstance(parsed_body, dict) else None

    payload = parsed_body if parsed_body is not None else raw_body
    return payload, parsed_body if isinstance(parsed_body, dict) else None


def _maybe_handle_slack_url_verification(
    parsed_body: dict[str, Any] | None,
) -> JSONResponse | None:
    if not parsed_body or parsed_body.get("type") != "url_verification":
        return None

    challenge = parsed_body.get("challenge")
    if not isinstance(challenge, str) or not challenge.strip():
        raise HTTPException(
            status_code=400,
            detail="Missing Slack challenge value",
        )
    return JSONResponse(content={"challenge": challenge})


def _build_webhook_state(
    graph_config: Mapping[str, Any],
    inputs: dict[str, Any],
    runtime_config: Mapping[str, Any] | None,
) -> Any:
    """Build initial state for webhook execution."""
    return build_initial_state(graph_config, inputs, runtime_config)


def _extract_immediate_response(
    final_state: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, bool]:
    """Extract immediate_response and should_process from workflow results.

    Returns:
        Tuple of (immediate_response dict or None, should_process bool).
        should_process indicates whether async processing should continue
        after returning the immediate response.
    """
    results = final_state.get("results", {})
    for node_result in results.values():
        if isinstance(node_result, dict) and "immediate_response" in node_result:
            immediate = node_result["immediate_response"]
            if isinstance(immediate, dict) and immediate.get("content") is not None:
                should_process = node_result.get("should_process", False)
                return immediate, should_process
    return None, False


def _should_try_immediate_response(
    query_params: Mapping[str, Any],
) -> bool:
    """Return whether to attempt an immediate-response workflow execution."""
    if not query_params:
        return False
    wecom_keys = {"msg_signature", "timestamp", "nonce"}
    return bool(wecom_keys.intersection(query_params.keys()))


async def _try_immediate_response(
    version: WorkflowVersion,
    inputs: dict[str, Any],
    vault: VaultDep,
) -> tuple[PlainTextResponse | JSONResponse | Response | None, bool]:
    """Execute workflow and check for immediate_response.

    Some workflows (e.g., WeCom) need to return an immediate HTTP response.
    This function executes the workflow synchronously and checks if any node
    returned an immediate_response.

    Returns:
        Tuple of (response or None, should_queue_async_run).
        - response: The HTTP response to return immediately, or None
        - should_queue_async_run: Whether to also queue an async workflow run
    """
    graph_config = version.graph
    settings = get_settings()

    credential_context = CredentialAccessContext(workflow_id=version.workflow_id)
    resolver = CredentialResolver(vault, context=credential_context)

    execution_id = f"immediate-response-check-{uuid4()}"
    stored_config = version.runnable_config
    merged_config = merge_runnable_configs(stored_config, None)
    runtime_config: RunnableConfig = merged_config.to_runnable_config(execution_id)
    state_config = merged_config.to_state_config(execution_id)

    try:
        with credential_resolution(resolver):
            async with create_checkpointer(settings) as checkpointer:
                async with create_graph_store(settings) as graph_store:
                    graph = build_graph(graph_config)
                    compiled = graph.compile(
                        checkpointer=checkpointer,
                        store=graph_store,
                    )
                    state = _build_webhook_state(graph_config, inputs, state_config)
                    final_state = await compiled.ainvoke(state, config=runtime_config)
    except Exception:
        # If workflow build or execution fails, fall back to normal async processing
        logger.debug(
            "Immediate response check skipped for workflow %s: graph build/run failed",
            version.workflow_id,
        )
        return None, True

    immediate, should_process = _extract_immediate_response(final_state)
    if immediate is None:
        return None, True

    content = immediate.get("content", "")
    content_type = immediate.get("content_type", "text/plain")
    status_code = immediate.get("status_code", 200)

    if content_type == "application/json":
        response = _build_json_immediate_response(content, status_code)
    else:
        response = PlainTextResponse(content=str(content), status_code=status_code)
    return response, should_process


def _build_json_immediate_response(
    content: Any, status_code: int
) -> JSONResponse | Response:
    """Return a JSON response from raw content or a JSON-encoded string."""
    if isinstance(content, dict | list):
        return JSONResponse(content=content, status_code=status_code)
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return Response(
                content=content,
                media_type="application/json",
                status_code=status_code,
            )
        return JSONResponse(content=parsed, status_code=status_code)
    return JSONResponse(content=content, status_code=status_code)


@router.put(
    "/workflows/{workflow_ref}/triggers/webhook/config",
    response_model=WebhookTriggerConfig,
)
async def configure_webhook_trigger(
    workflow_ref: str,
    request: WebhookTriggerConfig,
    repository: RepositoryDep,
    tenant: TenantContextDep,
) -> WebhookTriggerConfig:
    """Persist webhook trigger configuration for the workflow."""
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, tenant_id=str(tenant.tenant_id)
    )
    try:
        return await repository.configure_webhook_trigger(workflow_uuid, request)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)


@router.get(
    "/workflows/{workflow_ref}/triggers/webhook/config",
    response_model=WebhookTriggerConfig,
)
async def get_webhook_trigger_config(
    workflow_ref: str,
    repository: RepositoryDep,
    tenant: TenantContextDep,
) -> WebhookTriggerConfig:
    """Return the configured webhook trigger definition."""
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, tenant_id=str(tenant.tenant_id)
    )
    try:
        return await repository.get_webhook_trigger_config(workflow_uuid)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)


async def _queue_webhook_run(
    repository: RepositoryDep,
    workflow_id: UUID,
    method: str,
    headers: dict[str, str],
    query_params: dict[str, Any],
    payload: Any,
    source_ip: str | None,
) -> WorkflowRun:
    """Queue a webhook-triggered workflow run."""
    try:
        return await repository.handle_webhook_trigger(
            workflow_id,
            method=method,
            headers=headers,
            query_params=query_params,
            payload=payload,
            source_ip=source_ip,
        )
    except WebhookValidationError as exc:
        raise_webhook_error(exc)
    except CredentialHealthError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": str(exc), "failures": exc.report.failures},
        ) from exc


@public_webhook_router.get(
    "/workflows/{workflow_ref}/triggers/webhook",
    response_model=WorkflowRun,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="invoke_webhook_trigger_get",
)
@public_webhook_router.post(
    "/workflows/{workflow_ref}/triggers/webhook",
    response_model=WorkflowRun,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="invoke_webhook_trigger_post",
)
@public_webhook_router.put(
    "/workflows/{workflow_ref}/triggers/webhook",
    response_model=WorkflowRun,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="invoke_webhook_trigger_put",
)
@public_webhook_router.patch(
    "/workflows/{workflow_ref}/triggers/webhook",
    response_model=WorkflowRun,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="invoke_webhook_trigger_patch",
)
@public_webhook_router.delete(
    "/workflows/{workflow_ref}/triggers/webhook",
    response_model=WorkflowRun,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="invoke_webhook_trigger_delete",
)
async def invoke_webhook_trigger(
    workflow_ref: str,
    request: Request,
    repository: RepositoryDep,
    vault: VaultDep,
    preserve_raw_body: bool = Query(
        default=False,
        description="Store the raw request body alongside parsed payloads.",
    ),
) -> WorkflowRun | JSONResponse | PlainTextResponse | Response:
    """Validate inbound webhook data and enqueue a workflow run."""
    workflow_uuid = await resolve_workflow_ref_id(repository, workflow_ref)
    try:
        raw_body = await request.body()
    except Exception as exc:  # pragma: no cover - FastAPI handles body read
        raise HTTPException(
            status_code=400,
            detail="Failed to read request body",
        ) from exc

    headers = {key: value for key, value in request.headers.items()}
    query_params = dict(request.query_params)

    payload, parsed_body = _parse_webhook_body(
        raw_body, preserve_raw_body=preserve_raw_body
    )
    slack_response = _maybe_handle_slack_url_verification(parsed_body)
    if slack_response is not None:
        return slack_response

    webhook_inputs: dict[str, Any] = {
        "method": request.method,
        "headers": headers,
        "query_params": query_params,
        "body": payload,
    }

    # Check for immediate response (e.g., WeCom URL verification)
    immediate_response: PlainTextResponse | JSONResponse | Response | None = None
    should_queue = True
    if _should_try_immediate_response(query_params):
        try:
            version = await repository.get_latest_version(workflow_uuid)
            immediate_response, should_queue = await _try_immediate_response(
                version, webhook_inputs, vault
            )
        except WorkflowNotFoundError as exc:
            raise_not_found("Workflow not found", exc)
        except WorkflowVersionNotFoundError as exc:
            raise_not_found("Workflow version not found", exc)

    # Queue async run if workflow indicates processing is needed
    run: WorkflowRun | None = None
    if should_queue:
        source_ip = getattr(request.client, "host", None)
        run = await _queue_webhook_run(
            repository,
            workflow_uuid,
            request.method,
            headers,
            query_params,
            payload,
            source_ip,
        )

    # Return immediate response if available, otherwise return run info
    if immediate_response is not None:
        return immediate_response
    if run is not None:
        return run
    return JSONResponse(content={"status": "accepted"}, status_code=202)


@router.put(
    "/workflows/{workflow_ref}/triggers/cron/config",
    response_model=CronTriggerConfig,
)
async def configure_cron_trigger(
    workflow_ref: str,
    request: CronTriggerConfig,
    repository: RepositoryDep,
    tenant: TenantContextDep,
) -> CronTriggerConfig:
    """Persist cron trigger configuration for the workflow."""
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, tenant_id=str(tenant.tenant_id)
    )
    try:
        return await repository.configure_cron_trigger(workflow_uuid, request)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)


@router.get(
    "/workflows/{workflow_ref}/triggers/cron/config",
    response_model=CronTriggerConfig,
)
async def get_cron_trigger_config(
    workflow_ref: str,
    repository: RepositoryDep,
    tenant: TenantContextDep,
) -> CronTriggerConfig:
    """Return the configured cron trigger definition."""
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, tenant_id=str(tenant.tenant_id)
    )
    try:
        return await repository.get_cron_trigger_config(workflow_uuid)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except CronTriggerNotFoundError as exc:
        raise_not_found("Cron trigger not found", exc)


@router.delete(
    "/workflows/{workflow_ref}/triggers/cron/config",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def delete_cron_trigger(
    workflow_ref: str,
    repository: RepositoryDep,
    tenant: TenantContextDep,
) -> Response:
    """Remove the cron trigger configuration for the workflow."""
    workflow_uuid = await resolve_workflow_ref_id(
        repository, workflow_ref, tenant_id=str(tenant.tenant_id)
    )
    try:
        await repository.delete_cron_trigger(workflow_uuid)
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/triggers/cron/dispatch",
    response_model=list[WorkflowRun],
)
async def dispatch_cron_triggers(
    repository: RepositoryDep,
    request: CronDispatchRequest | None = None,
) -> list[WorkflowRun]:
    """Evaluate cron schedules and enqueue any due runs."""
    now = request.now if request else None
    try:
        runs = await repository.dispatch_due_cron_runs(now=now)
        return runs
    except CredentialHealthError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": str(exc), "failures": exc.report.failures},
        ) from exc


@router.post(
    "/triggers/manual/dispatch",
    response_model=list[WorkflowRun],
)
async def dispatch_manual_runs(
    request: ManualDispatchRequest,
    repository: RepositoryDep,
) -> list[WorkflowRun]:
    """Dispatch one or more manual workflow runs."""
    try:
        runs = await repository.dispatch_manual_runs(request)
        return runs
    except WorkflowNotFoundError as exc:
        raise_not_found("Workflow not found", exc)
    except WorkflowVersionNotFoundError as exc:
        raise_not_found("Workflow version not found", exc)
    except CredentialHealthError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": str(exc), "failures": exc.report.failures},
        ) from exc


__all__ = ["router", "public_webhook_router"]
