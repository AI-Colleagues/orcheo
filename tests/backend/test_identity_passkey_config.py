"""Tests for resolving the passkey (WebAuthn) relying party from settings."""

from __future__ import annotations

import pytest

from orcheo_backend.app.identity.config import resolve_passkey_relying_party


@pytest.mark.parametrize(
    ("studio_url", "rp_id", "origins", "expected"),
    [
        (
            "http://localhost:2026",
            None,
            None,
            ("localhost", ("http://localhost:2026",)),
        ),
        (
            "https://Orcheo.Example.com:443/studio",
            None,
            None,
            ("orcheo.example.com", ("https://orcheo.example.com",)),
        ),
        (
            "http://localhost:80",
            None,
            None,
            ("localhost", ("http://localhost",)),
        ),
        (
            "https://orcheo.example.com:8443",
            None,
            None,
            ("orcheo.example.com", ("https://orcheo.example.com:8443",)),
        ),
        (
            "http://studio.localhost:2026",
            None,
            None,
            ("studio.localhost", ("http://studio.localhost:2026",)),
        ),
        (
            "https://studio.example.com",
            " Example.com. ",
            None,
            ("example.com", ("https://studio.example.com",)),
        ),
        (
            "https://ignored.example.net",
            "example.com",
            "https://studio.example.com, https://example.com:443,"
            "https://studio.example.com",
            ("example.com", ("https://studio.example.com", "https://example.com")),
        ),
        (
            "https://ignored.example.net",
            None,
            ["https://a.example.com", " "],
            ("a.example.com", ("https://a.example.com",)),
        ),
        (
            "https://studio.example.com",
            None,
            "",
            ("studio.example.com", ("https://studio.example.com",)),
        ),
    ],
    ids=[
        "local-dev",
        "https-default-port",
        "http-default-port",
        "custom-port",
        "localhost-subdomain",
        "rp-id-override",
        "origin-list",
        "origin-iterable",
        "blank-origins",
    ],
)
def test_resolve_passkey_relying_party(
    studio_url: str,
    rp_id: str | None,
    origins: object,
    expected: tuple[str, tuple[str, ...]],
) -> None:
    assert (
        resolve_passkey_relying_party(studio_url, rp_id=rp_id, origins=origins)  # type: ignore[arg-type]
        == expected
    )


@pytest.mark.parametrize(
    ("studio_url", "rp_id", "message"),
    [
        ("http://orcheo.example.com", None, "not served over https"),
        ("http://127.0.0.1:2026", None, "not served over https"),
        ("https://10.0.0.5", None, "is an IP address"),
        ("https://studio.example.com", "10.0.0.5", "is an IP address"),
        ("https://studio.example.com", "other.com", "is not within RP ID"),
        ("https://studio.example.com", "dio.example.com", "is not within RP ID"),
        ("ftp://studio.example.com", None, "is not an http(s) URL"),
        ("", None, "is not an http(s) URL"),
        ("https://studio.example.com:port", None, "Port could not be cast"),
    ],
    ids=[
        "plain-http",
        "http-ip",
        "https-ip",
        "ip-rp-id",
        "foreign-rp-id",
        "suffix-but-not-subdomain",
        "bad-scheme",
        "empty",
        "bad-port",
    ],
)
def test_unusable_settings_turn_passkeys_off(
    studio_url: str,
    rp_id: str | None,
    message: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        assert resolve_passkey_relying_party(studio_url, rp_id=rp_id) == (None, ())

    assert "Passkeys are disabled" in caplog.text
    assert message in caplog.text
