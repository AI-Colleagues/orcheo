"""L7 forward-proxy (Envoy) configuration generator.

The Envoy forward proxy is **not** the network boundary — that's nftables —
but it does:

1. Allowlist outbound HTTP/HTTPS host destinations using one operator-owned
   global hostname set.
2. Emit a structured access log of every request, including denied hosts,
   that the audit consumer in ``orcheo.sandbox.egress.audit`` ingests.

Configuration is rendered as YAML for ``envoyproxy/envoy``. Operators reload
Envoy on workspace egress-allowlist changes.
"""

from __future__ import annotations
import os
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkspaceEgressAllowlist:
    """Deprecated workspace allowlist retained for configuration compatibility."""

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
        """Return the enforceable global allowlist for any workspace."""
        del workspace_id
        return self.global_allowed_hosts

    @classmethod
    def from_env(cls) -> EnvoyForwardProxyConfig:
        """Build a global allowlist from ``ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS``."""
        hosts = tuple(
            item.strip()
            for item in os.getenv("ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        )
        return cls(global_allowed_hosts=hosts)


def _listeners(config: EnvoyForwardProxyConfig) -> str:
    """Render the listener stanza."""
    allowed_domains = (
        ", ".join(f'"{host}", "{host}:*"' for host in config.global_allowed_hosts)
        or '"orcheo-no-external-hosts-configured.invalid"'
    )
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
        f"              route_config:\n"
        f"                name: sandbox_routes\n"
        f"                virtual_hosts:\n"
        f"                  - name: approved_hosts\n"
        f"                    domains: [{allowed_domains}]\n"
        f"                    routes:\n"
        f"                      - match: {{ connect_matcher: {{}} }}\n"
        f"                        route:\n"
        f"                          cluster: dynamic_forward_proxy_cluster\n"
        f"                          upgrade_configs:\n"
        f"                            - upgrade_type: CONNECT\n"
        f"                              connect_config: {{}}\n"
        f"                      - match: {{ prefix: '/' }}\n"
        f"                        route: {{ cluster: dynamic_forward_proxy_cluster }}\n"
        f"                  - name: deny_all\n"
        f"                    domains: ['*']\n"
        f"                    routes:\n"
        f"                      - match: {{ connect_matcher: {{}} }}\n"
        f"                        direct_response: {{ status: 403 }}\n"
        f"                      - match: {{ prefix: '/' }}\n"
        f"                        direct_response: {{ status: 403 }}\n"
        f"              http_filters:\n"
        f"                - name: envoy.filters.http.dynamic_forward_proxy\n"
        f"                  typed_config:\n"
        f"                    '@type': type.googleapis.com/envoy.extensions.filters."
        f"http.dynamic_forward_proxy.v3.FilterConfig\n"
        f"                    dns_cache_config: {{ name: sandbox_egress_dns_cache }}\n"
        f"                - name: envoy.filters.http.router\n"
        f"                  typed_config:\n"
        f"                    '@type': type.googleapis.com/envoy.extensions.filters."
        f"http.router.v3.Router\n"
        f"              access_log:\n"
        f"                - name: envoy.access_loggers.file\n"
        f"                  typed_config:\n"
        f"                    '@type': type.googleapis.com/envoy.extensions."
        f"access_loggers.file.v3.FileAccessLog\n"
        f"                    path: {config.audit_log_path}\n"
    )


def _clusters(config: EnvoyForwardProxyConfig) -> str:
    """Render a single dynamic-forward-proxy cluster."""
    del config
    return (
        "  - name: dynamic_forward_proxy_cluster\n"
        "    lb_policy: CLUSTER_PROVIDED\n"
        "    cluster_type:\n"
        "      name: envoy.clusters.dynamic_forward_proxy\n"
        "      typed_config:\n"
        "        '@type': type.googleapis.com/envoy.extensions.clusters."
        "dynamic_forward_proxy.v3.ClusterConfig\n"
        "        dns_cache_config: { name: sandbox_egress_dns_cache }\n"
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
