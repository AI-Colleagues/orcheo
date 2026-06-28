"""Tests for revoking CLI service tokens."""

from __future__ import annotations
import httpx
import respx
from typer.testing import CliRunner
from orcheo_sdk.cli.http import ApiClient
from orcheo_sdk.cli.main import app
from orcheo_sdk.services.service_tokens import revoke_service_token_data


def test_token_revoke(runner: CliRunner, env: dict[str, str]) -> None:
    """Test revoking a token."""
    with respx.mock(assert_all_called=True) as router:
        router.delete("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(204)
        )
        result = runner.invoke(
            app,
            ["token", "revoke", "token-123", "--reason", "No longer needed"],
            env=env,
        )

    assert result.exit_code == 0
    assert "revoked successfully" in result.stdout
    assert "No longer needed" in result.stdout


def test_token_revoke_security_breach(runner: CliRunner, env: dict[str, str]) -> None:
    """Test revoking a token due to security breach."""
    with respx.mock(assert_all_called=True) as router:
        router.delete("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(204)
        )
        result = runner.invoke(
            app,
            ["token", "revoke", "token-123", "-r", "Security breach detected"],
            env=env,
        )

    assert result.exit_code == 0
    assert "revoked successfully" in result.stdout
    assert "Security breach detected" in result.stdout


def test_revoke_service_token_data_with_message() -> None:
    """Test revoke_service_token_data when response contains a message field."""
    with respx.mock:
        respx.delete("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(
                200, json={"message": "Token revoked successfully"}
            )
        )

        client = ApiClient(
            base_url="http://api.test",
            token="test-token",
        )
        result = revoke_service_token_data(client, "token-123", "test reason")

        assert result["status"] == "success"
        assert result["message"] == "Token revoked successfully"


def test_token_revoke_machine_mode(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Machine mode outputs JSON success for token revoke."""
    import json

    with respx.mock(assert_all_called=True) as router:
        router.delete("http://api.test/api/admin/service-tokens/token-123").mock(
            return_value=httpx.Response(204)
        )
        result = runner.invoke(
            app,
            ["token", "revoke", "token-123", "--reason", "Expired"],
            env=machine_env,
        )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert "revoked" in data["message"]
