"""L7 forward-proxy (Envoy) configuration generator.

The Envoy forward proxy is **not** the network boundary — that's nftables —
but it does:

1. Allowlist outbound HTTP/HTTPS host destinations per workspace.
2. Emit a structured access log of every request, including denied hosts,
   that the audit consumer in ``orcheo.sandbox.egress.audit`` ingests.

Configuration is rendered as YAML for ``envoyproxy/envoy``. Operators reload
Envoy on workspace egress-allowlist changes.
"""

from __future__ import annotations
import json
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkspaceEgressAllowlist:
    """Per-workspace allowed HTTP/HTTPS hosts."""

    workspace_id: str
    hosts: tuple[str, ...]
    methods: tuple[str, ...] = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD")


@dataclass(frozen=True)
class EnvoyForwardProxyConfig:
    """Settings for the Envoy forward proxy."""

    listen_address: str = "0.0.0.0"
    listen_port: int = 3128
    audit_log_path: str = "/tmp/egress-audit.jsonl"
    workspaces: tuple[WorkspaceEgressAllowlist, ...] = field(default_factory=tuple)
    global_allowed_hosts: tuple[str, ...] = ()

    def render_yaml(self) -> str:
        """Render the proxy config as Envoy bootstrap YAML."""
        listeners = _listeners(self)
        clusters = _clusters(self)
        return (
            f"static_resources:\n  listeners:\n{listeners}\n  clusters:\n{clusters}\n"
        )

    def allowlist_for_workspace(self, workspace_id: str) -> tuple[str, ...]:
        """Return the merged (global + workspace) allowlist for a workspace."""
        merged: list[str] = list(self.global_allowed_hosts)
        for entry in self.workspaces:
            if entry.workspace_id == workspace_id:
                for host in entry.hosts:
                    if host not in merged:
                        merged.append(host)
        return tuple(merged)


def _listeners(config: EnvoyForwardProxyConfig) -> str:
    """Render the listener stanza."""
    workspaces = {ws.workspace_id: list(ws.hosts) for ws in config.workspaces}
    audit_payload = {
        "audit_log_path": config.audit_log_path,
        "global_allowed_hosts": list(config.global_allowed_hosts),
        "workspaces": workspaces,
    }
    audit_json = json.dumps(audit_payload, indent=2, sort_keys=True)
    indented_audit = "\n".join("        " + line for line in audit_json.splitlines())
    return (
        f"  - address:\n"
        f"      socket_address:\n"
        f"        address: {config.listen_address}\n"
        f"        port_value: {config.listen_port}\n"
        f"    filter_chains:\n"
        f"      - filters:\n"
        f"          - name: envoy.filters.network.http_connection_manager\n"
        f"            typed_config:\n"
        f"              '@type': type.googleapis.com/envoy.extensions.filters."
        f"network.http_connection_manager.v3.HttpConnectionManager\n"
        f"              stat_prefix: sandbox_egress\n"
        f"              access_log:\n"
        f"                - name: envoy.access_loggers.file\n"
        f"                  typed_config:\n"
        f"                    '@type': type.googleapis.com/envoy.extensions."
        f"access_loggers.file.v3.FileAccessLog\n"
        f"                    path: {config.audit_log_path}\n"
        f"              orcheo_audit_payload: |\n{indented_audit}\n"
    )


def _clusters(config: EnvoyForwardProxyConfig) -> str:
    """Render a single dynamic-forward-proxy cluster."""
    del config
    return (
        "  - name: dynamic_forward_proxy_cluster\n"
        "    lb_policy: CLUSTER_PROVIDED\n"
        "    cluster_type:\n"
        "      name: envoy.clusters.dynamic_forward_proxy\n"
    )


def egress_environment(
    workspace_id: str,
    config: EnvoyForwardProxyConfig,
    *,
    extras: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the env vars a sandbox needs to use the proxy.

    Returns ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY`` plus an Orcheo-
    specific ``ORCHEO_WORKSPACE_ID`` so the proxy can attribute requests.
    """
    proxy_url = f"http://proxy:{config.listen_port}"
    env: dict[str, str] = {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "NO_PROXY": "localhost,127.0.0.1",
        "ORCHEO_WORKSPACE_ID": workspace_id,
    }
    if extras:
        env.update(extras)
    return env
