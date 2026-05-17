"""Core exports for :mod:`orcheo_backend.app`."""

from __future__ import annotations
from pydantic import TypeAdapter as _PydanticTypeAdapter
import orcheo_backend.app.workflow_execution as _workflow_execution_module
from orcheo.config import get_settings
from orcheo.graph.builder import build_graph
from orcheo.persistence import create_checkpointer, create_graph_store
from orcheo_backend.app.authentication import (
    authenticate_request,
    authenticate_websocket,
)
from orcheo_backend.app.chatkit_runtime import (
    _CHATKIT_CLEANUP_INTERVAL_SECONDS,
    _chatkit_cleanup_task,
    _chatkit_server_ref,
    _coerce_int,
    get_chatkit_server,
)
from orcheo_backend.app.chatkit_runtime import (
    cancel_chatkit_cleanup_task as _cancel_chatkit_cleanup_task,
)
from orcheo_backend.app.chatkit_runtime import (
    chatkit_retention_days as _chatkit_retention_days,
)
from orcheo_backend.app.chatkit_runtime import (
    ensure_chatkit_cleanup_task as _ensure_chatkit_cleanup_task,
)
from orcheo_backend.app.chatkit_runtime import (
    get_chatkit_store as _get_chatkit_store,
)
from orcheo_backend.app.credential_utils import (
    alert_to_response as _alert_to_response,
)
from orcheo_backend.app.credential_utils import (
    build_oauth_tokens as _build_oauth_tokens,
)
from orcheo_backend.app.credential_utils import (
    build_policy as _build_policy,
)
from orcheo_backend.app.credential_utils import (
    build_scope as _build_scope,
)
from orcheo_backend.app.credential_utils import (
    credential_to_response as _credential_to_response,
)
from orcheo_backend.app.credential_utils import (
    infer_credential_access as _infer_credential_access,
)
from orcheo_backend.app.credential_utils import (
    policy_to_payload as _policy_to_payload,
)
from orcheo_backend.app.credential_utils import (
    scope_from_access as _scope_from_access,
)
from orcheo_backend.app.credential_utils import (
    scope_to_payload as _scope_to_payload,
)
from orcheo_backend.app.credential_utils import (
    template_to_response as _template_to_response,
)
from orcheo_backend.app.dependencies import (
    _create_repository,
    _credential_service_ref,
    _ensure_credential_service,
    _get_repository,
    _history_store_ref,
    _repository_ref,
    _vault_ref,
    get_credential_service,
    get_repository,
    get_vault,
)
from orcheo_backend.app.dependencies import (
    credential_context_from_workflow as _context_from_workflow,
)
from orcheo_backend.app.errors import (
    raise_conflict as _raise_conflict,
)
from orcheo_backend.app.errors import (
    raise_not_found as _raise_not_found,
)
from orcheo_backend.app.errors import (
    raise_scope_error as _raise_scope_error,
)
from orcheo_backend.app.errors import (
    raise_webhook_error as _raise_webhook_error,
)
from orcheo_backend.app.factory import create_app
from orcheo_backend.app.history_utils import (
    health_report_to_response as _health_report_to_response,
)
from orcheo_backend.app.history_utils import (
    history_to_response as _history_to_response,
)
from orcheo_backend.app.providers import (
    create_vault as _create_vault,
)
from orcheo_backend.app.providers import (
    settings_value as _settings_value,
)
from orcheo_backend.app.workflow_execution import (
    _log_final_state_debug,
    _log_sensitive_debug,
    _log_step_debug,
    _should_log_sensitive_debug,
    execute_workflow,
    execute_workflow_evaluation,
    execute_workflow_training,
)
from orcheo_backend.app.workflow_execution import logger as workflow_logger


TypeAdapter = _PydanticTypeAdapter
app = create_app()
logger = workflow_logger

__all__ = [
    "TypeAdapter",
    "app",
    "build_graph",
    "create_app",
    "create_checkpointer",
    "create_graph_store",
    "execute_workflow",
    "execute_workflow_evaluation",
    "execute_workflow_training",
    "get_chatkit_server",
    "get_credential_service",
    "get_repository",
    "get_settings",
    "get_vault",
    "logger",
    "authenticate_request",
    "authenticate_websocket",
    "_CHATKIT_CLEANUP_INTERVAL_SECONDS",
    "_alert_to_response",
    "_build_oauth_tokens",
    "_build_policy",
    "_build_scope",
    "_cancel_chatkit_cleanup_task",
    "_chatkit_cleanup_task",
    "_chatkit_retention_days",
    "_chatkit_server_ref",
    "_coerce_int",
    "_context_from_workflow",
    "_credential_service_ref",
    "_credential_to_response",
    "_create_repository",
    "_create_vault",
    "_ensure_chatkit_cleanup_task",
    "_ensure_credential_service",
    "_get_chatkit_store",
    "_get_repository",
    "_health_report_to_response",
    "_history_store_ref",
    "_history_to_response",
    "_infer_credential_access",
    "_log_final_state_debug",
    "_log_sensitive_debug",
    "_log_step_debug",
    "_workflow_execution_module",
    "_policy_to_payload",
    "_settings_value",
    "_raise_conflict",
    "_raise_not_found",
    "_raise_scope_error",
    "_raise_webhook_error",
    "_repository_ref",
    "_scope_from_access",
    "_scope_to_payload",
    "_should_log_sensitive_debug",
    "_template_to_response",
    "_vault_ref",
]
