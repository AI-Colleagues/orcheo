"""Shared exact-host and reserved-path checks for the app delivery gateway."""

from __future__ import annotations
import ipaddress
from urllib.parse import unquote
from orcheo.hosted_apps.errors import AliasValidationError
from orcheo.hosted_apps.models import normalize_alias


__all__ = ["canonical_app_host", "derive_client_ip", "is_safe_app_path"]


def canonical_app_host(host: str, base_domain: str) -> tuple[str, str]:
    """Return canonical ``(host, alias)`` only for one exact wildcard label."""
    raw_host = host.strip().lower()
    if raw_host.count(":") == 1:
        name, port = raw_host.rsplit(":", 1)
        if not port.isdigit():
            raise AliasValidationError("App host port is invalid.")
        raw_host = name
    candidate = raw_host.rstrip(".")
    domain = base_domain.strip().lower().rstrip(".")
    if not candidate or candidate != raw_host.rstrip("."):
        raise AliasValidationError("App host is not canonical.")
    suffix = f".{domain}"
    if not candidate.endswith(suffix):
        raise AliasValidationError("App host is outside the configured apps domain.")
    alias = candidate.removesuffix(suffix)
    if "." in alias:
        raise AliasValidationError("App host must have exactly one alias label.")
    normalize_alias(alias)
    return candidate, alias


def derive_client_ip(
    peer_ip: str,
    forwarded_for: str | None,
    *,
    trusted_proxy_cidrs: tuple[str, ...] = (),
    trusted_hops: int = 0,
) -> str:
    """Derive client IP only through an explicitly trusted proxy boundary."""
    try:
        peer = ipaddress.ip_address(peer_ip)
        networks = tuple(
            ipaddress.ip_network(value, strict=False) for value in trusted_proxy_cidrs
        )
    except ValueError as exc:
        raise ValueError("Trusted proxy IP configuration is invalid.") from exc
    if (
        not forwarded_for
        or trusted_hops <= 0
        or not any(peer in network for network in networks)
    ):
        return str(peer)
    chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    if len(chain) < trusted_hops:
        raise ValueError("Forwarded client IP chain is shorter than trusted hops.")
    try:
        return str(ipaddress.ip_address(chain[-trusted_hops]))
    except ValueError as exc:
        raise ValueError("Forwarded client IP is invalid.") from exc


def is_safe_app_path(path: str) -> bool:
    """Return whether a path cannot shadow runtime routes or confuse asset lookup."""
    decoded = unquote(path)
    return (
        decoded.startswith("/")
        and "\\" not in decoded
        and "\x00" not in decoded
        and not any(segment in {"", ".", ".."} for segment in decoded.split("/")[1:])
        and not decoded.startswith("/__orcheo/")
    )
