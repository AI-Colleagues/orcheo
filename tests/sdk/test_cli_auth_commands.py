"""CLI auth command tests."""

from __future__ import annotations
import time
from pathlib import Path
import pytest
from typer.testing import CliRunner
from orcheo_sdk.cli.auth.tokens import AuthTokens, set_oauth_tokens
from orcheo_sdk.cli.main import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def auth_env(tmp_path: Path) -> dict[str, str]:
    config_dir = tmp_path / "config"
    cache_dir = tmp_path / "cache"
    config_dir.mkdir()
    cache_dir.mkdir()
    return {
        "ORCHEO_API_URL": "http://api.test",
        "ORCHEO_CONFIG_DIR": str(config_dir),
        "ORCHEO_CACHE_DIR": str(cache_dir),
        "ORCHEO_SERVICE_TOKEN": "",
        "ORCHEO_HUMAN": "1",
        "NO_COLOR": "1",
    }


def test_auth_status_not_authenticated(
    runner: CliRunner, auth_env: dict[str, str]
) -> None:
    result = runner.invoke(app, ["auth", "status"], env=auth_env)
    assert result.exit_code == 0
    assert "Not authenticated" in result.output


def test_auth_status_with_service_token(
    runner: CliRunner, auth_env: dict[str, str]
) -> None:
    auth_env["ORCHEO_SERVICE_TOKEN"] = "service-token-12345678"
    result = runner.invoke(app, ["auth", "status"], env=auth_env)
    assert result.exit_code == 0
    assert "Service Token" in result.output
    assert "service-" in result.output  # Token prefix shown


def test_auth_status_with_oauth_token(
    runner: CliRunner, auth_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set config dir before storing tokens
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", auth_env["ORCHEO_CONFIG_DIR"])

    future = int(time.time() * 1000) + 3600000
    tokens = AuthTokens(
        access_token="oauth-access-token",
        expires_at=future,
    )
    set_oauth_tokens(profile="default", tokens=tokens)

    result = runner.invoke(app, ["auth", "status"], env=auth_env)
    assert result.exit_code == 0
    assert "OAuth" in result.output


def test_auth_logout_success(
    runner: CliRunner, auth_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", auth_env["ORCHEO_CONFIG_DIR"])

    tokens = AuthTokens(access_token="oauth-token")
    set_oauth_tokens(profile="default", tokens=tokens)

    result = runner.invoke(app, ["auth", "logout"], env=auth_env)
    assert result.exit_code == 0
    assert "Logged out" in result.output


def test_auth_login_missing_config(
    runner: CliRunner, auth_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # OAuth not configured (missing ORCHEO_AUTH_ISSUER and ORCHEO_AUTH_CLIENT_ID)
    # Must explicitly clear these in case they're set in the environment
    monkeypatch.delenv("ORCHEO_AUTH_ISSUER", raising=False)
    monkeypatch.delenv("ORCHEO_AUTH_CLIENT_ID", raising=False)

    result = runner.invoke(app, ["auth", "login"], env=auth_env)
    assert result.exit_code == 1
    # Error may be in output or exception
    error_text = result.output or str(result.exception)
    assert "OAuth not configured" in error_text


def test_auth_login_machine_error(
    runner: CliRunner, auth_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Machine mode login errors should output JSON."""
    import json
    from orcheo_sdk.cli.errors import CLIError

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise CLIError("OAuth not configured.")

    monkeypatch.setattr("orcheo_sdk.cli.auth.commands.start_oauth_login", _raise)

    machine_env = {k: v for k, v in auth_env.items() if k != "ORCHEO_HUMAN"}
    result = runner.invoke(app, ["auth", "login"], env=machine_env)
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["error"] == "OAuth not configured."


def test_auth_logout_machine_output(
    runner: CliRunner, auth_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test logout in machine mode outputs JSON."""
    import json

    monkeypatch.setenv("ORCHEO_CONFIG_DIR", auth_env["ORCHEO_CONFIG_DIR"])
    tokens = AuthTokens(access_token="oauth-token")
    set_oauth_tokens(profile="default", tokens=tokens)

    machine_env = {k: v for k, v in auth_env.items() if k != "ORCHEO_HUMAN"}
    result = runner.invoke(app, ["auth", "logout"], env=machine_env)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "success"
    assert "Logged out" in data["message"]


def test_auth_status_machine_not_authenticated(
    runner: CliRunner, auth_env: dict[str, str]
) -> None:
    """Test auth status machine mode when not authenticated."""
    import json

    machine_env = {k: v for k, v in auth_env.items() if k != "ORCHEO_HUMAN"}
    result = runner.invoke(app, ["auth", "status"], env=machine_env)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "not_authenticated"
    assert "profile" in data


def test_auth_status_machine_with_service_token(
    runner: CliRunner, auth_env: dict[str, str]
) -> None:
    """Test auth status machine mode with service token."""
    import json

    machine_env = {k: v for k, v in auth_env.items() if k != "ORCHEO_HUMAN"} | {
        "ORCHEO_SERVICE_TOKEN": "svc-token-12345678"
    }
    result = runner.invoke(app, ["auth", "status"], env=machine_env)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "authenticated"
    assert data["method"] == "service_token"


def test_auth_status_machine_with_oauth_token(
    runner: CliRunner, auth_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test auth status machine mode with valid OAuth token."""
    import json

    monkeypatch.setenv("ORCHEO_CONFIG_DIR", auth_env["ORCHEO_CONFIG_DIR"])

    future = int(time.time() * 1000) + 3600000
    tokens = AuthTokens(access_token="oauth-access-token", expires_at=future)
    set_oauth_tokens(profile="default", tokens=tokens)

    machine_env = {k: v for k, v in auth_env.items() if k != "ORCHEO_HUMAN"}
    result = runner.invoke(app, ["auth", "status"], env=machine_env)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "authenticated"
    assert data["method"] == "oauth"
    assert "expires" in data
