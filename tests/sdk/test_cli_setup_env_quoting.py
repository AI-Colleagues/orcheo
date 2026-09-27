"""Tests for quoting and single-line validation of generated .env values."""

from __future__ import annotations
from pathlib import Path
import pytest
import typer
from rich.console import Console
from orcheo_sdk.cli import setup as setup_mod
from orcheo_sdk.cli.setup import SmtpEmailConfig, quote_env_value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("plain-value", "plain-value"),
        ("Orcheo <no-reply@orcheo.cloud>", "Orcheo <no-reply@orcheo.cloud>"),
        ("pa$s #word", "'pa$s #word'"),
        ('say "hi"', "'say \"hi\"'"),
        ("back\\slash", "'back\\slash'"),
        ("it's #fine", '"it\'s #fine"'),
    ],
)
def test_quote_env_value(value: str, expected: str) -> None:
    assert quote_env_value("KEY", value) == expected


@pytest.mark.parametrize("value", ["it's $x", 'it\'s "x"', "it's \\x"])
def test_quote_env_value_rejects_unrepresentable_values(value: str) -> None:
    with pytest.raises(typer.BadParameter, match="KEY cannot combine a single quote"):
        quote_env_value("KEY", value)


def test_smtp_secrets_round_trip_through_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("")
    updates = setup_mod.build_smtp_env_updates(
        SmtpEmailConfig(
            host="smtp.example.com",
            port=587,
            username="it's-me",
            password="pa$s #word",
            from_email=None,
            use_tls=True,
        )
    )

    setup_mod._upsert_env_values(env_file, updates, console=Console(record=True))

    lines = env_file.read_text().splitlines()
    assert "ORCHEO_SMTP_PASSWORD='pa$s #word'" in lines
    assert 'ORCHEO_SMTP_USERNAME="it\'s-me"' in lines
    assert setup_mod._read_env_value(env_file, "ORCHEO_SMTP_PASSWORD") == ("pa$s #word")
    assert setup_mod._read_env_value(env_file, "ORCHEO_SMTP_USERNAME") == "it's-me"


@pytest.mark.parametrize("value", ["line\nORCHEO_AUTH_MODE=disabled", "a\rb"])
def test_upsert_env_values_rejects_multiline_values(tmp_path: Path, value: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=1\n")

    with pytest.raises(typer.BadParameter, match="ORCHEO_SMTP_PASSWORD must be"):
        setup_mod._upsert_env_values(
            env_file, {"ORCHEO_SMTP_PASSWORD": value}, console=Console()
        )
    with pytest.raises(typer.BadParameter, match="GENERATED must be a single line"):
        setup_mod._upsert_env_values(
            env_file, {}, defaults={"GENERATED": value}, console=Console()
        )
    assert env_file.read_text() == "EXISTING=1\n"
