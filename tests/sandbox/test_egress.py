"""Tests for the egress policy and forward-proxy generators."""

from __future__ import annotations
import io
import logging
from orcheo.sandbox.audit import SandboxAuditLogger
from orcheo.sandbox.egress import (
    EgressAuditConsumer,
    EgressPolicy,
    EnvoyForwardProxyConfig,
    build_nftables_ruleset,
)
from orcheo.sandbox.egress.policy import (
    host_ips_for_denied_hostnames,
    render_security_group_rules,
)
from orcheo.sandbox.egress.proxy import (
    WorkspaceEgressAllowlist,
    egress_environment,
)


def test_nftables_ruleset_allows_only_relay_and_proxy_then_drops() -> None:
    """The rendered ruleset permits the two child-facing service ports only."""
    policy = EgressPolicy(
        denied_cidrs=("169.254.0.0/16",),
        denied_hostnames=("redis", "postgres"),
        resolved_host_ips=("10.0.1.5", "10.0.1.6"),
        proxy_ip="10.0.1.2",
        proxy_port=3128,
    )
    rendered = build_nftables_ruleset(policy)
    assert "ip daddr 10.99.0.2 tcp dport 9091 accept" in rendered
    assert "ip daddr 10.0.1.2 tcp dport 3128 accept" in rendered
    assert 'iifname "sandbox0" ip saddr 10.99.0.128/25 drop' in rendered


def test_nftables_ruleset_does_not_open_extra_cidrs() -> None:
    """Legacy extra CIDRs cannot weaken the hardened default policy."""
    policy = EgressPolicy(extra_allowed_cidrs=("10.99.0.0/24",))
    rendered = build_nftables_ruleset(policy)
    assert "ip daddr 10.99.0.0/24 accept" not in rendered


def test_security_group_rules_mirror_l3_l4_denylist() -> None:
    """Backstop SG rules cover every IP-level deny."""
    policy = EgressPolicy(
        denied_cidrs=("169.254.0.0/16",),
        resolved_host_ips=("10.0.1.5",),
    )
    rules = render_security_group_rules(policy)
    assert {rule["cidr_block"] for rule in rules} == {
        "169.254.0.0/16",
        "10.0.1.5",
    }
    assert all(rule["action"] == "deny" for rule in rules)


def test_host_ips_for_denied_hostnames_uses_injected_resolver() -> None:
    """Custom resolver lets tests avoid real DNS."""

    def resolver(host: str) -> list[str]:
        return {"redis": ["10.0.1.5"], "postgres": ["10.0.1.6", "10.0.1.7"]}.get(
            host, []
        )

    ips = host_ips_for_denied_hostnames(("redis", "postgres", "unknown"), resolver)
    assert ips == ("10.0.1.5", "10.0.1.6", "10.0.1.7")


def test_host_ips_for_denied_hostnames_deduplicates_shared_ips() -> None:
    """IPs that appear in multiple hostname resolutions are included only once."""

    def resolver(host: str) -> list[str]:
        # Both hosts resolve to the same IP — deduplication branch (128->127).
        return {"host-a": ["10.0.2.1"], "host-b": ["10.0.2.1", "10.0.2.2"]}.get(
            host, []
        )

    ips = host_ips_for_denied_hostnames(("host-a", "host-b"), resolver)
    assert ips == ("10.0.2.1", "10.0.2.2")


def test_envoy_config_renders_global_allowlist_only() -> None:
    """The proxy enforces only the operator-owned global hostname set."""
    config = EnvoyForwardProxyConfig(
        workspaces=(
            WorkspaceEgressAllowlist(workspace_id="ws-a", hosts=("api.example.com",)),
        ),
        global_allowed_hosts=("api.openai.com",),
    )
    rendered = config.render_yaml()
    assert "api.example.com" not in rendered
    assert "api.openai.com" in rendered
    assert "sandbox_egress" in rendered
    assert "connect_matcher" in rendered
    assert "upgrade_type: CONNECT" in rendered


def test_egress_environment_sets_proxy_vars() -> None:
    """Sandboxes get HTTP_PROXY/HTTPS_PROXY plus a workspace tag."""
    config = EnvoyForwardProxyConfig(listen_port=3128)
    env = egress_environment("ws", config, extras={"X_EXTRA": "1"})
    assert env["HTTP_PROXY"] == "http://proxy:3128"
    assert env["HTTPS_PROXY"] == "http://proxy:3128"
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert env["ORCHEO_WORKSPACE_ID"] == "ws"
    assert env["X_EXTRA"] == "1"


def test_audit_consumer_emits_denied_events(caplog: object) -> None:
    """Status 403 lines become egress_denied audit records."""
    capture = caplog  # type: ignore[assignment]
    audit = SandboxAuditLogger("orcheo.sandbox.audit.test.egress")
    consumer = EgressAuditConsumer(audit)
    lines = [
        '{"response_code": 200, "host": "api.openai.com", "workspace_id": "ws"}',
        '{"response_code": 403, "host": "169.254.169.254", "workspace_id": "ws"}',
        "not json",
        "",
    ]
    with capture.at_level(  # type: ignore[attr-defined]
        logging.INFO, logger="orcheo.sandbox.audit.test.egress"
    ):
        emitted = consumer.consume(iter(lines))
    assert emitted == 1
    records = [
        r
        for r in capture.records  # type: ignore[attr-defined]
        if r.name == "orcheo.sandbox.audit.test.egress"
    ]
    assert records[0].sandbox_event == "egress_denied"  # type: ignore[attr-defined]


def test_audit_consumer_can_log_allowed(caplog: object) -> None:
    """log_allowed=True surfaces permitted traffic as audit records too."""
    capture = caplog  # type: ignore[assignment]
    audit = SandboxAuditLogger("orcheo.sandbox.audit.test.egress2")
    consumer = EgressAuditConsumer(audit, log_allowed=True)
    line = '{"response_code": 200, "host": "ok.example", "workspace_id": "ws"}'
    with capture.at_level(  # type: ignore[attr-defined]
        logging.INFO, logger="orcheo.sandbox.audit.test.egress2"
    ):
        consumer.consume(io.StringIO(line + "\n"))
    records = [
        r
        for r in capture.records  # type: ignore[attr-defined]
        if r.name == "orcheo.sandbox.audit.test.egress2"
    ]
    assert records[0].sandbox_event == "egress_allowed"  # type: ignore[attr-defined]


def test_allowlist_for_workspace_uses_global_policy_only() -> None:
    """Per-workspace entries no longer weaken the enforced global policy."""
    config = EnvoyForwardProxyConfig(
        workspaces=(
            WorkspaceEgressAllowlist(workspace_id="a", hosts=("x", "y")),
            WorkspaceEgressAllowlist(workspace_id="b", hosts=("z",)),
        ),
        global_allowed_hosts=("g", "x"),
    )
    assert config.allowlist_for_workspace("a") == ("g", "x")
    assert config.allowlist_for_workspace("missing") == ("g", "x")


def test_proxy_global_allowlist_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator-facing environment surface populates the proxy policy."""
    monkeypatch.setenv(
        "ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS", "api.openai.com, api.example.com"
    )
    config = EnvoyForwardProxyConfig.from_env()
    assert config.global_allowed_hosts == ("api.openai.com", "api.example.com")


def test_host_ips_for_denied_hostnames_uses_real_dns_for_loopback() -> None:
    """host_ips_for_denied_hostnames resolves hostnames via the system resolver."""
    # "localhost" is universally resolvable without network access.
    ips = host_ips_for_denied_hostnames(("localhost",))
    assert len(ips) > 0


def test_host_ips_for_denied_hostnames_ignores_unresolvable_hosts() -> None:
    """Hostnames that cannot be resolved are skipped without raising."""
    ips = host_ips_for_denied_hostnames(
        ("this.host.does.not.exist.orcheo.invalid",),
    )
    # An unresolvable host produces an empty tuple (no IPs).
    assert isinstance(ips, tuple)


def test_egress_environment_without_extras_omits_update() -> None:
    """egress_environment returns only the standard proxy vars when extras is None."""
    config = EnvoyForwardProxyConfig(listen_port=3128)
    env = egress_environment("ws", config)
    assert set(env.keys()) == {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ORCHEO_WORKSPACE_ID",
    }


def test_sandbox_settings_pool_max_validator_rejects_zero() -> None:
    """_validate_pool_max raises ValueError when given a value less than 1."""
    from orcheo.sandbox.config import SandboxSettings
    import pytest

    with pytest.raises(ValueError, match="default_pool_max must be >= 1"):
        SandboxSettings._validate_pool_max(0)
