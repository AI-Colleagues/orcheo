"""L3/L4 egress policy generator (nftables).

This module emits the nftables ruleset attached to the sandbox network
namespace. The ruleset is **default-deny** for tenant-source traffic: it
allows only the credential relay and HTTP/HTTPS proxy, then drops every
other packet from the tenant sandbox allocation range.

This generator is intentionally string-only — no runtime Python invokes
``nft`` directly. The deploy automation writes the rendered ruleset to
``/etc/nftables.d/sandbox-egress.nft`` and reloads nftables. That keeps the
boundary independent of tenant code: even if the sandbox somehow escaped to
its own networking stack, the host kernel still drops the packet.
"""

from __future__ import annotations
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from orcheo.sandbox.config import DEFAULT_DENY_CIDRS, DEFAULT_DENY_HOSTNAMES


@dataclass(frozen=True)
class EgressPolicy:
    """Resolved L3/L4 deny policy ready to render to nftables."""

    denied_cidrs: tuple[str, ...] = DEFAULT_DENY_CIDRS
    denied_hostnames: tuple[str, ...] = DEFAULT_DENY_HOSTNAMES
    resolved_host_ips: tuple[str, ...] = ()
    proxy_ip: str = "127.0.0.1"
    proxy_port: int = 3128
    credential_relay_ip: str = "10.99.0.2"
    credential_relay_port: int = 9091
    sandbox_source_cidr: str = "10.99.0.128/25"
    interface: str = "sandbox0"
    extra_allowed_cidrs: tuple[str, ...] = field(default_factory=tuple)

    def all_denied_ip_targets(self) -> tuple[str, ...]:
        """Return every IP-level target that must be dropped."""
        return tuple(self.denied_cidrs) + tuple(self.resolved_host_ips)


def build_nftables_ruleset(policy: EgressPolicy) -> str:
    """Render ``policy`` as an nftables script.

    Args:
        policy: Resolved egress policy.

    Returns:
        A single ``nftables`` script suitable for ``nft -f``.
    """
    return (
        _TEMPLATE.format(
            interface=policy.interface,
            proxy_ip=policy.proxy_ip,
            proxy_port=policy.proxy_port,
            relay_ip=policy.credential_relay_ip,
            relay_port=policy.credential_relay_port,
            source_cidr=policy.sandbox_source_cidr,
        ).strip()
        + "\n"
    )


def render_security_group_rules(policy: EgressPolicy) -> list[dict[str, object]]:
    """Render the policy as AWS-style security-group egress rules (backstop).

    Returns a list of dicts in the shape consumed by Terraform / CloudFormation.
    Each entry denies a CIDR. The security-group backstop is in addition to —
    not in place of — the nftables ruleset.
    """
    rules: list[dict[str, object]] = []
    for target in policy.all_denied_ip_targets():
        rules.append(
            {
                "type": "egress",
                "protocol": "-1",
                "from_port": 0,
                "to_port": 0,
                "cidr_block": target,
                "action": "deny",
            }
        )
    return rules


def host_ips_for_denied_hostnames(
    hostnames: Iterable[str],
    resolver: Callable[[str], list[str]] | None = None,
) -> tuple[str, ...]:
    """Resolve denied hostnames to their A-record IPs at deploy time.

    Args:
        hostnames: Hostnames to resolve.
        resolver: Optional resolver injected for testing. Defaults to
            ``socket.gethostbyname_ex``.

    Returns:
        A flat tuple of resolved IPs (deduplicated, order-preserving).
    """
    import socket

    seen: list[str] = []
    seen_set: set[str] = set()
    for host in hostnames:
        if resolver is not None:
            ips = resolver(host)
        else:
            try:
                _, _, ips = socket.gethostbyname_ex(host)
            except OSError:
                ips = []
        for ip in ips:
            if ip not in seen_set:
                seen.append(ip)
                seen_set.add(ip)
    return tuple(seen)


_TEMPLATE = """
table inet orcheo_sandbox {{
  chain forward {{
    type filter hook forward priority -10; policy accept;
    iifname "{interface}" ip saddr {source_cidr} \
ip daddr {relay_ip} tcp dport {relay_port} accept
    iifname "{interface}" ip saddr {source_cidr} \
ip daddr {proxy_ip} tcp dport {proxy_port} accept
    iifname "{interface}" ip saddr {source_cidr} drop
    iifname "{interface}" ip6 saddr != :: drop
  }}
}}
"""
