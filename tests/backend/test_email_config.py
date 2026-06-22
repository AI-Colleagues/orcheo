"""Tests for transactional email configuration helpers."""

from __future__ import annotations

from orcheo_backend.app import email_config


def test_build_smtp_settings_returns_none_without_host(
    monkeypatch,
) -> None:
    monkeypatch.setattr(email_config, "get_settings", lambda: {})

    assert email_config.build_smtp_settings() is None


def test_build_smtp_settings_parses_string_tls_and_defaults(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        email_config,
        "get_settings",
        lambda: {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "2525",
            "SMTP_USERNAME": "mailer",
            "SMTP_PASSWORD": "secret",
            "SMTP_USE_TLS": "off",
        },
    )

    settings = email_config.build_smtp_settings()
    assert settings is not None
    assert settings.host == "smtp.example.com"
    assert settings.port == 2525
    assert settings.username == "mailer"
    assert settings.password == "secret"
    assert settings.use_tls is False


def test_build_smtp_settings_uses_default_tls_for_non_boolean_values(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        email_config,
        "get_settings",
        lambda: {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USE_TLS": None,
        },
    )

    settings = email_config.build_smtp_settings()
    assert settings is not None
    assert settings.use_tls is True


def test_build_smtp_settings_preserves_boolean_tls_values(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        email_config,
        "get_settings",
        lambda: {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USE_TLS": False,
        },
    )

    settings = email_config.build_smtp_settings()
    assert settings is not None
    assert settings.use_tls is False
