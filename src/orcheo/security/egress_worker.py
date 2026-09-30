"""Apply fail-closed network rules before dropping privileges for the proxy."""

from __future__ import annotations
import os
import subprocess


_BLOCKED_IPV4 = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.88.99.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
)
_BLOCKED_IPV6 = (
    "::/128",
    "::1/128",
    "::ffff:0:0/96",
    "64:ff9b::/96",
    "64:ff9b:1::/48",
    "100::/64",
    "2001::/23",
    "2001:db8::/32",
    "2002::/16",
    "3fff::/20",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
)


def firewall_commands() -> list[list[str]]:
    """Allow Docker DNS, replies, and public HTTPS only, for both IP families."""
    commands: list[list[str]] = []
    for binary, blocked in (("iptables", _BLOCKED_IPV4), ("ip6tables", _BLOCKED_IPV6)):
        commands.extend(
            [
                [binary, "-w", "-P", "OUTPUT", "DROP"],
                [binary, "-w", "-F", "OUTPUT"],
                [
                    binary,
                    "-w",
                    "-A",
                    "OUTPUT",
                    "-m",
                    "conntrack",
                    "--ctstate",
                    "ESTABLISHED,RELATED",
                    "-j",
                    "ACCEPT",
                ],
            ]
        )
        if binary == "iptables":
            for protocol in ("udp", "tcp"):
                commands.append(
                    [
                        binary,
                        "-w",
                        "-A",
                        "OUTPUT",
                        "-d",
                        "127.0.0.11",
                        "-p",
                        protocol,
                        "-m",
                        "conntrack",
                        "--ctorigdstport",
                        "53",
                        "-j",
                        "ACCEPT",
                    ]
                )
        for subnet in blocked:
            commands.append(
                [binary, "-w", "-A", "OUTPUT", "-d", subnet, "-j", "REJECT"]
            )
        allow = [binary, "-w", "-A", "OUTPUT", "-p", "tcp", "--dport", "443"]
        if binary == "ip6tables":
            allow.extend(["-d", "2000::/3"])
        commands.append([*allow, "-j", "ACCEPT"])
    return commands


def main() -> None:
    """Refuse startup if either firewall cannot be applied; then drop root."""
    for command in firewall_commands():
        subprocess.run(command, check=True)
    os.execvp(
        "gosu", ["gosu", "orcheo", "python", "-m", "orcheo.security.https_proxy_worker"]
    )


if __name__ == "__main__":
    main()
