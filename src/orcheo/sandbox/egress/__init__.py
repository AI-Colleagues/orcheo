"""Sandbox network-egress controls — L3/L4 default-deny + L7 forward proxy.

The boundary that actually keeps tenant code off the metadata endpoint,
Redis, and Postgres is the L3/L4 default-deny implemented as an nftables
ruleset attached to the sandbox network namespace (with EC2 security groups
as a backstop). Permitted outbound HTTP/HTTPS is then funneled through the
Envoy L7 forward proxy, which performs host allowlisting and emits an
audit-event stream for denied hosts.

The functions in this package generate the *configuration* for those two
layers — they're operator-facing artifacts written to the sandbox host's
networking stack at deploy time. The runtime audit hooks in
``orcheo.sandbox.egress.audit`` consume the proxy's denied-host log and
emit the same ``SandboxAuditEvent`` payloads the manager uses for lifecycle.
"""

from orcheo.sandbox.egress.audit import EgressAuditConsumer
from orcheo.sandbox.egress.policy import EgressPolicy, build_nftables_ruleset
from orcheo.sandbox.egress.proxy import EnvoyForwardProxyConfig


__all__ = [
    "EgressAuditConsumer",
    "EgressPolicy",
    "EnvoyForwardProxyConfig",
    "build_nftables_ruleset",
]
