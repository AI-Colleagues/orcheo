"""Tests for CLI service token creation commands."""

from __future__ import annotations
import httpx
import respx
from typer.testing import CliRunner
from orcheo_sdk.cli.main import app


def test_token_create_minimal(runner: CliRunner, env: dict[str, str]) -> None:
    """Test creating a token with minimal options."""
    response_data = {
        "identifier": "token-123",
        "secret": "secret-abc-xyz",
    }

    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "create"], env=env)

    assert result.exit_code == 0
    assert "token-123" in result.stdout
    assert "secret-abc-xyz" in result.stdout
    assert "Store this secret securely" in result.stdout


def test_token_create_with_name(runner: CliRunner, env: dict[str, str]) -> None:
    """Test creating a token with a custom name."""
    response_data = {
        "identifier": "generated-id",
        "name": "my-custom-token",
        "secret": "secret-123",
    }

    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(
            app, ["token", "create", "--name", "my-custom-token"], env=env
        )

    assert result.exit_code == 0
    assert "my-custom-token" in result.stdout


def test_token_create_with_scopes(runner: CliRunner, env: dict[str, str]) -> None:
    """Test creating a token with scopes."""
    response_data = {
        "identifier": "scoped-token",
        "secret": "secret-123",
        "scopes": ["read:workflows", "write:workflows"],
    }

    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(
            app,
            [
                "token",
                "create",
                "--scope",
                "read:workflows",
                "--scope",
                "write:workflows",
            ],
            env=env,
        )

    assert result.exit_code == 0
    assert "read:workflows" in result.stdout
    assert "write:workflows" in result.stdout


def test_token_create_with_workspaces(runner: CliRunner, env: dict[str, str]) -> None:
    """Test creating a token with workspace restrictions."""
    response_data = {
        "identifier": "workspace-token",
        "secret": "secret-123",
        "workspace_ids": ["ws-1", "ws-2"],
    }

    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(
            app,
            ["token", "create", "--workspace", "ws-1", "--workspace", "ws-2"],
            env=env,
        )

    assert result.exit_code == 0
    assert "ws-1" in result.stdout
    assert "ws-2" in result.stdout


def test_token_create_with_expiration(runner: CliRunner, env: dict[str, str]) -> None:
    """Test creating a token with expiration."""
    response_data = {
        "identifier": "expiring-token",
        "secret": "secret-123",
        "expires_at": "2024-12-31T23:59:59Z",
    }

    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(
            app, ["token", "create", "--expires-in", "3600"], env=env
        )

    assert result.exit_code == 0
    assert "2024-12-31" in result.stdout


def test_token_create_with_all_options(runner: CliRunner, env: dict[str, str]) -> None:
    """Test creating a token with all options."""
    response_data = {
        "identifier": "full-token",
        "secret": "secret-123",
        "scopes": ["read:all"],
        "workspace_ids": ["ws-1"],
        "expires_at": "2024-12-31T23:59:59Z",
    }

    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(
            app,
            [
                "token",
                "create",
                "--name",
                "full-token",
                "--scope",
                "read:all",
                "--workspace",
                "ws-1",
                "--expires-in",
                "3600",
            ],
            env=env,
        )

    assert result.exit_code == 0
    assert "full-token" in result.stdout
    assert "secret-123" in result.stdout
    assert "read:all" in result.stdout
    assert "ws-1" in result.stdout


def test_token_create_machine_mode(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Machine mode outputs JSON for token create."""
    import json

    response_data = {
        "identifier": "token-123",
        "secret": "secret-abc-xyz",
    }

    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/admin/service-tokens").mock(
            return_value=httpx.Response(200, json=response_data)
        )
        result = runner.invoke(app, ["token", "create"], env=machine_env)

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["identifier"] == "token-123"
    assert data["secret"] == "secret-abc-xyz"
