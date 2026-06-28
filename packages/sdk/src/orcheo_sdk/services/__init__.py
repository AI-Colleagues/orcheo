"""Service layer for Orcheo SDK.

This module contains the core business logic for interacting with the Orcheo API.
Services are pure functions that operate on data and are reused by CLI
command modules.
"""

from orcheo_sdk.services.agent_tools import (
    list_agent_tools_data,
    load_tool_registry,
    show_agent_tool_data,
)
from orcheo_sdk.services.codegen import (
    generate_workflow_scaffold_data,
    generate_workflow_template_data,
)
from orcheo_sdk.services.credentials import (
    create_credential_data,
    delete_credential_data,
    list_credentials_data,
)
from orcheo_sdk.services.edges import list_edges_data, show_edge_data
from orcheo_sdk.services.nodes import list_nodes_data, show_node_data
from orcheo_sdk.services.plugins import (
    disable_plugin_data,
    doctor_plugins_data,
    enable_plugin_data,
    install_plugin_data,
    list_plugins_data,
    preview_disable_plugin_data,
    preview_enable_plugin_data,
    preview_uninstall_plugin_data,
    preview_update_all_plugins_data,
    preview_update_plugin_data,
    show_plugin_data,
    uninstall_plugin_data,
    update_all_plugins_data,
    update_plugin_data,
)
from orcheo_sdk.services.service_tokens import (
    create_service_token_data,
    list_service_tokens_data,
    revoke_service_token_data,
    show_service_token_data,
)
from orcheo_sdk.services.workflows import (
    delete_workflow_data,
    download_workflow_data,
    enrich_workflow_publish_metadata,
    get_latest_workflow_version_data,
    get_workflow_credential_readiness_data,
    list_workflows_data,
    pause_workflow_listener_data,
    publish_workflow_data,
    resume_workflow_listener_data,
    run_workflow_data,
    save_workflow_runnable_config_data,
    schedule_workflow_cron,
    show_workflow_data,
    sync_cron_schedule_if_changed,
    unpublish_workflow_data,
    unschedule_workflow_cron,
    update_workflow_data,
    upload_workflow_data,
)


__all__ = [
    # Workflows
    "list_workflows_data",
    "show_workflow_data",
    "run_workflow_data",
    "pause_workflow_listener_data",
    "resume_workflow_listener_data",
    "delete_workflow_data",
    "upload_workflow_data",
    "update_workflow_data",
    "download_workflow_data",
    "get_latest_workflow_version_data",
    "get_workflow_credential_readiness_data",
    "publish_workflow_data",
    "unpublish_workflow_data",
    "enrich_workflow_publish_metadata",
    "save_workflow_runnable_config_data",
    "schedule_workflow_cron",
    "sync_cron_schedule_if_changed",
    "unschedule_workflow_cron",
    # Nodes
    "list_nodes_data",
    "show_node_data",
    # Edges
    "list_edges_data",
    "show_edge_data",
    # Plugins
    "list_plugins_data",
    "show_plugin_data",
    "install_plugin_data",
    "preview_update_plugin_data",
    "update_plugin_data",
    "preview_update_all_plugins_data",
    "update_all_plugins_data",
    "preview_uninstall_plugin_data",
    "uninstall_plugin_data",
    "preview_enable_plugin_data",
    "enable_plugin_data",
    "preview_disable_plugin_data",
    "disable_plugin_data",
    "doctor_plugins_data",
    # Credentials
    "list_credentials_data",
    "create_credential_data",
    "delete_credential_data",
    # Code generation
    "generate_workflow_scaffold_data",
    "generate_workflow_template_data",
    # Agent tools
    "list_agent_tools_data",
    "show_agent_tool_data",
    "load_tool_registry",
    # Service tokens
    "list_service_tokens_data",
    "show_service_token_data",
    "create_service_token_data",
    "revoke_service_token_data",
]
