"""Sign-in email-domain allowlist parsing and matching.

Shared by the identity service, which enforces ``AUTH_ALLOWED_EMAIL_DOMAINS``,
and the installer, which validates the value before writing it.
"""

from __future__ import annotations
import re
from collections.abc import Iterable, Sequence


__all__ = ["is_email_domain_allowed", "parse_email_domains"]

_DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_PATTERN = re.compile(rf"(?=.{{1,253}}$){_DOMAIN_LABEL}(?:\.{_DOMAIN_LABEL})+")


def parse_email_domains(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Parse a comma-separated allowlist into unique, lower-cased domains.

    A leading ``@`` is accepted and dropped, and blank entries are ignored.
    Raises ``ValueError`` for an entry that is not a DNS domain name.
    """
    if value is None:
        return ()
    entries = value.split(",") if isinstance(value, str) else [str(v) for v in value]
    domains: list[str] = []
    for entry in entries:
        domain = entry.strip().lower().removeprefix("@")
        if not domain:
            continue
        if not _DOMAIN_PATTERN.fullmatch(domain):
            msg = f"Invalid email domain: {entry.strip()!r}"
            raise ValueError(msg)
        domains.append(domain)
    return tuple(dict.fromkeys(domains))


def is_email_domain_allowed(email: str, allowed_domains: Sequence[str]) -> bool:
    """Return whether ``email`` may sign in under the allowlist.

    An empty allowlist allows every domain. Matching is exact, so subdomains
    must be listed separately.
    """
    if not allowed_domains:
        return True
    _, separator, domain = email.rpartition("@")
    return bool(separator) and domain.lower() in allowed_domains
