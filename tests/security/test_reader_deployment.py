"""Check the public reader's credential and network boundaries."""

from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any
import pytest
import yaml
from orcheo.security import egress_worker


def test_lean_reader_cannot_inherit_worker_credentials_or_network() -> None:
    compose = yaml.safe_load(
        (
            Path(__file__).resolve().parents[2] / "deploy/lean/docker-compose.yml"
        ).read_text()
    )
    services = compose["services"]
    for name in ("public-browser", "public-egress"):
        service = services[name]
        assert "env_file" not in service
        assert "volumes" not in service
        assert "ports" not in service
        assert service["read_only"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert "default" not in service["networks"]
        assert set(service["environment"]) == {"HOME"}
    assert "public-egress" not in services["public-browser"]["networks"]
    assert compose["networks"]["reader-control"]["internal"]
    assert compose["networks"]["reader-egress"]["internal"]
    assert services["public-browser"]["user"] == "1000:1000"
    assert "default" in services["worker"]["networks"]
    assert "ORCHEO_POSTGRES_DSN" in services["worker"]["environment"]


def test_firewall_blocks_private_ranges_and_non_https() -> None:
    commands = egress_worker.firewall_commands()
    for binary in ("iptables", "ip6tables"):
        rules = [command for command in commands if command[0] == binary]
        assert rules[0] == [binary, "-w", "-P", "OUTPUT", "DROP"]
        assert rules[-1][-2:] == ["-j", "ACCEPT"]
        assert rules[-1][rules[-1].index("--dport") + 1] == "443"
    for subnet in (
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
        "64:ff9b::/96",
    ):
        assert any(
            subnet in command and command[-1] == "REJECT" for command in commands
        )
    dns = [command for command in commands if "127.0.0.11" in command]
    assert len(dns) == 2
    # Docker DNATs port 53 to its resolver's ephemeral local port before the
    # OUTPUT filter. Match the original port, not the translated one.
    assert all("--ctorigdstport" in command for command in dns)


def test_firewall_failure_does_not_start_proxy(monkeypatch: Any) -> None:
    started = []

    def fail(*args: Any, **kwargs: Any) -> None:
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(egress_worker.subprocess, "run", fail)
    monkeypatch.setattr(egress_worker.os, "execvp", lambda *args: started.append(args))
    with pytest.raises(subprocess.CalledProcessError):
        egress_worker.main()
    assert not started


def test_proxy_drops_root_after_applying_every_rule(monkeypatch: Any) -> None:
    applied = []
    started = []
    monkeypatch.setattr(
        egress_worker.subprocess,
        "run",
        lambda command, **kwargs: applied.append((command, kwargs)),
    )
    monkeypatch.setattr(egress_worker.os, "execvp", lambda *args: started.append(args))
    egress_worker.main()
    assert applied == [
        (command, {"check": True}) for command in egress_worker.firewall_commands()
    ]
    assert started == [
        (
            "gosu",
            ["gosu", "orcheo", "python", "-m", "orcheo.security.https_proxy_worker"],
        )
    ]
