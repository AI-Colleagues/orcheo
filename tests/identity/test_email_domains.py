"""Tests for sign-in email-domain allowlist parsing and matching."""

from __future__ import annotations
import pytest
from orcheo.identity.email_domains import is_email_domain_allowed, parse_email_domains


def test_parse_email_domains_normalizes_and_deduplicates() -> None:
    assert parse_email_domains(None) == ()
    assert parse_email_domains("") == ()
    assert parse_email_domains(" Example.com, @b.org,,example.COM ") == (
        "example.com",
        "b.org",
    )
    assert parse_email_domains(["A.io", "a.io"]) == ("a.io",)


@pytest.mark.parametrize(
    "value", ["localhost", "example..com", "-bad.com", "exa mple.com", "*.com"]
)
def test_parse_email_domains_rejects_invalid_entries(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid email domain"):
        parse_email_domains(f"example.com,{value}")


def test_is_email_domain_allowed_matches_exact_domains() -> None:
    allowed = ("example.com",)
    assert is_email_domain_allowed("anyone@other.org", ())
    assert is_email_domain_allowed("alice@Example.com", allowed)
    assert not is_email_domain_allowed("alice@sub.example.com", allowed)
    assert not is_email_domain_allowed("alice@example.com.evil.org", allowed)
    assert not is_email_domain_allowed("no-at-sign", allowed)
