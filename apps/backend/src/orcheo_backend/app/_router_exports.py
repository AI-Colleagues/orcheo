# mypy: ignore-errors
"""Router-level exports for :mod:`orcheo_backend.app`."""

from __future__ import annotations
from collections.abc import Callable
from typing import Any
from orcheo_backend.app.routers import (
    agentensor as _agentensor_routes,
)
from orcheo_backend.app.routers import (
    chatkit as _chatkit_routes,
)
from orcheo_backend.app.routers import (
    credential_alerts as _credential_alerts_routes,
)
from orcheo_backend.app.routers import (
    credential_health as _credential_health_routes,
)
from orcheo_backend.app.routers import (
    credential_templates as _credential_templates_routes,
)
from orcheo_backend.app.routers import (
    credentials as _credentials_routes,
)
from orcheo_backend.app.routers import (
    listeners as _listeners_routes,
)
from orcheo_backend.app.routers import (
    nodes as _nodes_routes,
)
from orcheo_backend.app.routers import (
    runs as _runs_routes,
)
from orcheo_backend.app.routers import (
    triggers as _triggers_routes,
)
from orcheo_backend.app.routers import (
    workflows as _workflows_routes,
)
from orcheo_backend.app.routers.websocket import workflow_websocket


chatkit_gateway: Callable[..., Any] = (  # type: ignore[assignment]
    _chatkit_routes.chatkit_gateway
)
create_chatkit_session_endpoint: Callable[..., Any] = (  # type: ignore[assignment]
    _chatkit_routes.create_chatkit_session_endpoint
)
trigger_chatkit_workflow: Callable[..., Any] = (  # type: ignore[assignment]
    _chatkit_routes.trigger_chatkit_workflow
)
_resolve_chatkit_workspace_id = _chatkit_routes._resolve_chatkit_workspace_id

list_credentials = _credentials_routes.list_credentials
create_credential = _credentials_routes.create_credential
reveal_credential_secret = _credentials_routes.reveal_credential_secret
update_credential = _credentials_routes.update_credential
delete_credential = _credentials_routes.delete_credential

list_credential_templates = _credential_templates_routes.list_credential_templates
create_credential_template = _credential_templates_routes.create_credential_template
get_credential_template = _credential_templates_routes.get_credential_template
update_credential_template = _credential_templates_routes.update_credential_template
delete_credential_template = _credential_templates_routes.delete_credential_template
issue_credential_from_template = (
    _credential_templates_routes.issue_credential_from_template
)

list_governance_alerts = _credential_alerts_routes.list_governance_alerts
acknowledge_governance_alert = _credential_alerts_routes.acknowledge_governance_alert

get_workflow_credential_readiness = (
    _credential_health_routes.get_workflow_credential_readiness
)
get_workflow_credential_health = (
    _credential_health_routes.get_workflow_credential_health
)
validate_workflow_credentials = _credential_health_routes.validate_workflow_credentials
list_workflow_listeners = _listeners_routes.list_workflow_listeners
get_workflow_listener_metrics = _listeners_routes.get_workflow_listener_metrics
pause_workflow_listener = _listeners_routes.pause_workflow_listener
resume_workflow_listener = _listeners_routes.resume_workflow_listener

list_workflows = _workflows_routes.list_workflows
create_workflow = _workflows_routes.create_workflow
get_workflow = _workflows_routes.get_workflow
get_workflow_canvas = _workflows_routes.get_workflow_canvas
update_workflow = _workflows_routes.update_workflow
archive_workflow = _workflows_routes.archive_workflow
publish_workflow = _workflows_routes.publish_workflow
revoke_workflow_publish = _workflows_routes.revoke_workflow_publish
create_workflow_chatkit_session = _workflows_routes.create_workflow_chatkit_session
ingest_workflow_version = _workflows_routes.ingest_workflow_version
update_workflow_version_runnable_config = (
    _workflows_routes.update_workflow_version_runnable_config
)
list_workflow_versions = _workflows_routes.list_workflow_versions
get_workflow_version = _workflows_routes.get_workflow_version
diff_workflow_versions = _workflows_routes.diff_workflow_versions

create_workflow_run = _runs_routes.create_workflow_run
list_workflow_runs = _runs_routes.list_workflow_runs
get_workflow_run = _runs_routes.get_workflow_run
list_workflow_execution_histories = _runs_routes.list_workflow_execution_histories
get_execution_history = _runs_routes.get_execution_history
replay_execution = _runs_routes.replay_execution
mark_run_started = _runs_routes.mark_run_started
mark_run_succeeded = _runs_routes.mark_run_succeeded
mark_run_failed = _runs_routes.mark_run_failed
mark_run_cancelled = _runs_routes.mark_run_cancelled

configure_webhook_trigger = _triggers_routes.configure_webhook_trigger
get_webhook_trigger_config = _triggers_routes.get_webhook_trigger_config
invoke_webhook_trigger = _triggers_routes.invoke_webhook_trigger
dispatch_cron_triggers = _triggers_routes.dispatch_cron_triggers
dispatch_manual_runs = _triggers_routes.dispatch_manual_runs
configure_cron_trigger = _triggers_routes.configure_cron_trigger
get_cron_trigger_config = _triggers_routes.get_cron_trigger_config
delete_cron_trigger = _triggers_routes.delete_cron_trigger

execute_node_endpoint = _nodes_routes.execute_node_endpoint
list_agentensor_checkpoints = _agentensor_routes.list_agentensor_checkpoints
get_agentensor_checkpoint = _agentensor_routes.get_agentensor_checkpoint

__all__ = [
    "acknowledge_governance_alert",
    "archive_workflow",
    "publish_workflow",
    "revoke_workflow_publish",
    "create_workflow_chatkit_session",
    "chatkit_gateway",
    "configure_cron_trigger",
    "configure_webhook_trigger",
    "create_chatkit_session_endpoint",
    "create_credential",
    "create_credential_template",
    "create_workflow",
    "create_workflow_run",
    "delete_credential",
    "delete_credential_template",
    "diff_workflow_versions",
    "dispatch_cron_triggers",
    "dispatch_manual_runs",
    "delete_cron_trigger",
    "execute_node_endpoint",
    "get_cron_trigger_config",
    "get_agentensor_checkpoint",
    "get_credential_template",
    "get_execution_history",
    "get_webhook_trigger_config",
    "get_workflow",
    "get_workflow_canvas",
    "get_workflow_credential_readiness",
    "get_workflow_credential_health",
    "get_workflow_listener_metrics",
    "get_workflow_run",
    "get_workflow_version",
    "ingest_workflow_version",
    "invoke_webhook_trigger",
    "issue_credential_from_template",
    "list_credential_templates",
    "list_credentials",
    "list_governance_alerts",
    "list_workflow_listeners",
    "list_workflow_execution_histories",
    "list_workflow_runs",
    "list_workflow_versions",
    "list_workflows",
    "list_agentensor_checkpoints",
    "mark_run_cancelled",
    "mark_run_failed",
    "mark_run_started",
    "mark_run_succeeded",
    "replay_execution",
    "reveal_credential_secret",
    "pause_workflow_listener",
    "resume_workflow_listener",
    "trigger_chatkit_workflow",
    "update_credential_template",
    "update_credential",
    "update_workflow",
    "update_workflow_version_runnable_config",
    "validate_workflow_credentials",
    "workflow_websocket",
    "_resolve_chatkit_workspace_id",
]
