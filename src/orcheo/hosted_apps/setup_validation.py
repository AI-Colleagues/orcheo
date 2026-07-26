"""Preflight validation for opt-in Hosted Apps stack deployments."""

from __future__ import annotations
import ipaddress
import os
import socket
from pathlib import Path
from orcheo.hosted_apps.config import HostedAppsSettings


__all__ = ["validate_hosted_apps_setup"]


def validate_hosted_apps_setup(  # noqa: C901, PLR0912
    environment: dict[str, str] | None = None, *, check_dns: bool = True
) -> list[str]:
    """Return validated setup facts or raise before starting an unsafe stack."""
    values = dict(os.environ) if environment is None else environment
    settings = HostedAppsSettings.from_environment(values)
    if not settings.enabled:
        raise ValueError("Hosted Apps must be explicitly enabled for preflight.")
    secret = values.get("ORCHEO_APP_GATEWAY_SECRET", "")
    if len(secret) < 32:
        raise ValueError("ORCHEO_APP_GATEWAY_SECRET must be at least 32 characters.")
    if not values.get("ORCHEO_POSTGRES_DSN"):
        raise ValueError(
            "ORCHEO_POSTGRES_DSN is required for durable runtime generation."
        )
    queue = values.get("ORCHEO_HOSTED_APPS_VALIDATION_QUEUE", "").strip()
    if not queue:
        raise ValueError("The dedicated Hosted Apps validation queue is required.")
    cidrs = [
        value.strip()
        for value in values.get("ORCHEO_APP_TRUSTED_PROXY_CIDRS", "").split(",")
        if value.strip()
    ]
    hops = int(values.get("ORCHEO_APP_TRUSTED_PROXY_HOPS", "0"))
    if bool(cidrs) != (hops > 0):
        raise ValueError("Trusted proxy CIDRs and hop count must be set together.")
    for cidr in cidrs:
        ipaddress.ip_network(cidr, strict=False)
    tls_method = values.get("ORCHEO_APP_TLS_METHOD", "local").strip().lower()
    if tls_method not in {"local", "provided", "dns-01"}:
        raise ValueError("ORCHEO_APP_TLS_METHOD must be local, provided, or dns-01.")
    if tls_method == "provided":
        for name in ("ORCHEO_APP_TLS_CERT_FILE", "ORCHEO_APP_TLS_KEY_FILE"):
            path = Path(values.get(name, "")).expanduser()
            if not path.is_file():
                raise ValueError(f"{name} must reference a readable file.")
    if tls_method == "dns-01" and not values.get("ORCHEO_APP_DNS_PROVIDER"):
        raise ValueError("DNS-01 TLS requires ORCHEO_APP_DNS_PROVIDER.")
    if settings.bundle_backend == "s3":
        required = (
            "ORCHEO_APP_S3_ENDPOINT_URL",
            "ORCHEO_APP_S3_REGION",
            "ORCHEO_APP_S3_BUCKET",
            "ORCHEO_APP_S3_ACCESS_KEY_ID",
            "ORCHEO_APP_S3_SECRET_ACCESS_KEY",
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise ValueError(f"S3 bundle storage is incomplete: {', '.join(missing)}")
    if check_dns and settings.base_domain is not None:
        try:
            socket.getaddrinfo(
                f"orcheo-preflight.{settings.base_domain}", 443, type=socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise ValueError(
                "Wildcard app DNS did not resolve for a probe alias."
            ) from exc
    return [
        f"base_domain={settings.base_domain}",
        f"bundle_backend={settings.bundle_backend}",
        f"validation_queue={queue}",
        f"tls_method={tls_method}",
        f"trusted_proxy_hops={hops}",
    ]


def main() -> None:  # pragma: no cover - console entry point
    """Run the setup preflight as a stack/operator command."""
    for fact in validate_hosted_apps_setup():
        print(fact)
