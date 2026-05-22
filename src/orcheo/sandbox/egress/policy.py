"""L3/L4 egress policy generator (nftables).

This module emits the nftables ruleset attached to the sandbox network
namespace. The ruleset is **default-deny**: it drops every packet whose
destination matches the denied CIDRs (link-local metadata, internal RFC1918
ranges hosting Redis/Postgres, etc.) and the denied hostnames (resolved to
IPs at deploy time). All other outbound packets are NAT'd to the Envoy
forward proxy on the host network namespace, which performs L7 allowlisting.

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
    deny_lines = (
        "\n".join(
            f"    ip daddr {target} drop" for target in policy.all_denied_ip_targets()
        )
        or "    # no IPv4 deny entries"
    )
    deny6_lines = (
        "\n".join(
            f"    ip6 daddr {target} drop"
            for target in policy.denied_cidrs
            if ":" in target
        )
        or "    # no IPv6 deny entries"
    )
    allow_lines = "\n".join(
        f"    ip daddr {cidr} accept" for cidr in policy.extra_allowed_cidrs
    )
    return (
        _TEMPLATE.format(
            interface=policy.interface,
            proxy_ip=policy.proxy_ip,
            proxy_port=policy.proxy_port,
            deny_lines=deny_lines,
            deny6_lines=deny6_lines,
            allow_lines=allow_lines or "    # no extra allow entries",
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
  chain output {{
    type filter hook output priority 0; policy accept;
    oifname "{interface}" jump sandbox_egress
  }}

  chain sandbox_egress {{
{deny_lines}
{deny6_lines}
{allow_lines}
    ip daddr 127.0.0.0/8 drop
    ip6 daddr ::1 drop
    tcp dport {{ 80, 443 }} ip daddr != {proxy_ip} drop
    ip daddr {proxy_ip} tcp dport {proxy_port} accept
    return
  }}
}}
"""
