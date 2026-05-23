"""L7 forward-proxy (Envoy) configuration generator.

The Envoy forward proxy is **not** the network boundary — that's nftables —
but it does:

1. Allowlist outbound HTTP/HTTPS host destinations using one operator-owned
   global hostname set.
2. Emit a structured access log of every request, including denied hosts,
   that the audit consumer in ``orcheo.sandbox.egress.audit`` ingests.

Configuration is rendered as YAML for ``envoyproxy/envoy``. Operators reload
Envoy on workspace egress-allowlist changes.

A literal ``*`` entry in the global allowlist is treated as "allow every
host" — the renderer emits a single approved virtual host matching all
domains and drops the trailing deny-all block. Use this for development
stacks. nftables and the gVisor/runc sandbox still enforce the network
boundary; the proxy just stops gating outbound HTTP on a host allowlist.
"""

from __future__ import annotations
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
import yaml


_WILDCARD_HOST = "*"
_PLACEHOLDER_HOST = "orcheo-no-external-hosts-configured.invalid"
_AUDIT_LOG_PATH = "/tmp/egress-audit.jsonl"
_DNS_RESOLVER_ADDRESS = "8.8.8.8"
_DNS_CACHE_NAME = "sandbox_egress_dns_cache"


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
    audit_log_path: str = _AUDIT_LOG_PATH
    workspaces: tuple[WorkspaceEgressAllowlist, ...] = field(default_factory=tuple)
    global_allowed_hosts: tuple[str, ...] = ()

    def render_yaml(self) -> str:
        """Render the proxy config as Envoy bootstrap YAML."""
        return yaml.safe_dump(
            self._build_config(), sort_keys=False, default_flow_style=False
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

    def _is_wildcard(self) -> bool:
        return _WILDCARD_HOST in self.global_allowed_hosts

    def _approved_domains(self) -> list[str]:
        if self._is_wildcard():
            return [_WILDCARD_HOST]
        if not self.global_allowed_hosts:
            return [_PLACEHOLDER_HOST]
        domains: list[str] = []
        for host in self.global_allowed_hosts:
            domains.extend((host, f"{host}:*"))
        return domains

    def _virtual_hosts(self) -> list[dict[str, Any]]:
        approved = {
            "name": "approved_hosts",
            "domains": self._approved_domains(),
            "routes": [
                {
                    "match": {"connect_matcher": {}},
                    "route": {
                        "cluster": "dynamic_forward_proxy_cluster",
                        "upgrade_configs": [
                            {"upgrade_type": "CONNECT", "connect_config": {}}
                        ],
                    },
                },
                {
                    "match": {"prefix": "/"},
                    "route": {"cluster": "dynamic_forward_proxy_cluster"},
                },
            ],
        }
        if self._is_wildcard():
            return [approved]
        deny_all = {
            "name": "deny_all",
            "domains": ["*"],
            "routes": [
                {
                    "match": {"connect_matcher": {}},
                    "direct_response": {"status": 403},
                },
                {"match": {"prefix": "/"}, "direct_response": {"status": 403}},
            ],
        }
        return [approved, deny_all]

    def _dns_cache_config(self) -> dict[str, Any]:
        return {
            "name": _DNS_CACHE_NAME,
            "dns_lookup_family": "V4_ONLY",
            "dns_cache_circuit_breaker": {"max_pending_requests": 1024},
            "typed_dns_resolver_config": {
                "name": "envoy.network.dns_resolver.cares",
                "typed_config": {
                    "@type": (
                        "type.googleapis.com/envoy.extensions."
                        "network.dns_resolver.cares.v3.CaresDnsResolverConfig"
                    ),
                    "resolvers": [
                        {
                            "socket_address": {
                                "address": _DNS_RESOLVER_ADDRESS,
                                "port_value": 53,
                            }
                        }
                    ],
                    "dns_resolver_options": {
                        "use_tcp_for_dns_lookups": True,
                        "no_default_search_domain": True,
                    },
                },
            },
        }

    def _listener(self) -> dict[str, Any]:
        return {
            "name": "sandbox_egress_listener",
            "address": {
                "socket_address": {
                    "address": self.listen_address,
                    "port_value": self.listen_port,
                }
            },
            "filter_chains": [
                {
                    "filters": [
                        {
                            "name": "envoy.filters.network.http_connection_manager",
                            "typed_config": {
                                "@type": (
                                    "type.googleapis.com/envoy.extensions."
                                    "filters.network.http_connection_manager."
                                    "v3.HttpConnectionManager"
                                ),
                                "stat_prefix": "sandbox_egress",
                                "route_config": {
                                    "name": "local_route",
                                    "virtual_hosts": self._virtual_hosts(),
                                },
                                "http_filters": [
                                    {
                                        "name": (
                                            "envoy.filters.http.dynamic_forward_proxy"
                                        ),
                                        "typed_config": {
                                            "@type": (
                                                "type.googleapis.com/envoy.extensions."
                                                "filters.http.dynamic_forward_proxy."
                                                "v3.FilterConfig"
                                            ),
                                            "dns_cache_config": (
                                                self._dns_cache_config()
                                            ),
                                        },
                                    },
                                    {
                                        "name": "envoy.filters.http.router",
                                        "typed_config": {
                                            "@type": (
                                                "type.googleapis.com/envoy.extensions."
                                                "filters.http.router.v3.Router"
                                            )
                                        },
                                    },
                                ],
                                "access_log": [
                                    {
                                        "name": "envoy.access_loggers.file",
                                        "typed_config": {
                                            "@type": (
                                                "type.googleapis.com/envoy.extensions."
                                                "access_loggers.file.v3.FileAccessLog"
                                            ),
                                            "path": self.audit_log_path,
                                            "typed_json_format": {
                                                "response_code": "%RESPONSE_CODE%",
                                                "host": "%REQ(:AUTHORITY)%",
                                                "workspace_id": (
                                                    "%REQ(X-ORCHEO-WORKSPACE)%"
                                                ),
                                                "sandbox_id": (
                                                    "%REQ(X-ORCHEO-SANDBOX-ID)%"
                                                ),
                                                "run_id": "%REQ(X-ORCHEO-RUN-ID)%",
                                            },
                                        },
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

    def _cluster(self) -> dict[str, Any]:
        return {
            "name": "dynamic_forward_proxy_cluster",
            "lb_policy": "CLUSTER_PROVIDED",
            "circuit_breakers": {
                "thresholds": [
                    {
                        "priority": "DEFAULT",
                        "max_connections": 1024,
                        "max_pending_requests": 1024,
                        "max_requests": 1024,
                        "max_retries": 3,
                    }
                ]
            },
            "cluster_type": {
                "name": "envoy.clusters.dynamic_forward_proxy",
                "typed_config": {
                    "@type": (
                        "type.googleapis.com/envoy.extensions.clusters."
                        "dynamic_forward_proxy.v3.ClusterConfig"
                    ),
                    "dns_cache_config": self._dns_cache_config(),
                },
            },
        }

    def _build_config(self) -> dict[str, Any]:
        return {
            "admin": {
                "access_log_path": "/tmp/envoy-admin-access.log",
                "address": {
                    "socket_address": {"address": "127.0.0.1", "port_value": 9901}
                },
            },
            "static_resources": {
                "listeners": [self._listener()],
                "clusters": [self._cluster()],
            },
        }


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
