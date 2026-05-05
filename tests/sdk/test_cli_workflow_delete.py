"""Workflow delete CLI command tests."""

from __future__ import annotations
import httpx
import respx
from typer.testing import CliRunner
from orcheo_sdk.cli.errors import APICallError, CLIError
from orcheo_sdk.cli.main import app


def test_workflow_delete_with_force(runner: CliRunner, env: dict[str, str]) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.delete("http://api.test/api/workflows/wf-1").mock(
            return_value=httpx.Response(204)
        )
        result = runner.invoke(
            app,
            ["workflow", "delete", "wf-1", "--force"],
            env=env,
        )
    assert result.exit_code == 0
    assert "deleted successfully" in result.stdout


def test_workflow_delete_without_force_prompts(
    runner: CliRunner, env: dict[str, str]
) -> None:
    # Test without --force which would prompt for confirmation
    # We'll simulate the user aborting
    with respx.mock:
        result = runner.invoke(
            app,
            ["workflow", "delete", "wf-1"],
            env=env,
            input="n\n",  # No to confirmation
        )
    # Typer.confirm with abort=True will exit with code 1 when user says no
    assert result.exit_code == 1


def test_workflow_delete_with_confirmation(
    runner: CliRunner, env: dict[str, str]
) -> None:
    # Test that delete succeeds when user confirms
    with respx.mock(assert_all_called=True) as router:
        router.delete("http://api.test/api/workflows/wf-1").mock(
            return_value=httpx.Response(204)
        )
        result = runner.invoke(
            app,
            ["workflow", "delete", "wf-1"],
            env=env,
            input="y\n",  # Yes to confirmation
        )
    assert result.exit_code == 0
    assert "deleted successfully" in result.stdout


def test_workflow_delete_offline_error(runner: CliRunner, env: dict[str, str]) -> None:
    offline_args = ["--offline", "workflow", "delete", "wf-1", "--force"]
    result = runner.invoke(app, offline_args, env=env)
    assert result.exit_code != 0
    assert isinstance(result.exception, CLIError)
    assert "network connectivity" in str(result.exception)


def test_workflow_delete_custom_message(runner: CliRunner, env: dict[str, str]) -> None:
    """Workflow delete with message that doesn't include 'deleted successfully'."""
    with respx.mock(assert_all_called=True) as router:
        # Mock delete to return a response (even though it's 204)
        router.delete("http://api.test/api/workflows/wf-1").mock(
            return_value=httpx.Response(204)
        )
        result = runner.invoke(
            app,
            ["workflow", "delete", "wf-1", "--force"],
            env=env,
        )
    assert result.exit_code == 0
    # Should show the default message since delete returns no content
    assert "wf-1" in result.stdout


def test_workflow_delete_with_success_message(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Workflow delete when API returns a message containing 'deleted successfully'."""
    success_message = "Workflow 'wf-1' deleted successfully from system"

    with respx.mock(assert_all_called=False) as router:
        # Mock the delete API call to return a 200 with a message
        router.delete("http://api.test/api/workflows/wf-1").mock(
            return_value=httpx.Response(200, json={"message": success_message})
        )
        result = runner.invoke(
            app,
            ["workflow", "delete", "wf-1", "--force"],
            env=env,
        )
    assert result.exit_code == 0
    assert success_message in result.stdout


def test_workflow_delete_blocks_managed_vibe_workflow(
    runner: CliRunner, env: dict[str, str]
) -> None:
    """Workflow delete is rejected for the managed Orcheo Vibe workflow."""
    with respx.mock(assert_all_called=True) as router:
        router.delete("http://api.test/api/workflows/wf-vibe").mock(
            return_value=httpx.Response(
                409,
                json={
                    "detail": {
                        "message": (
                            "The managed Orcheo Vibe workflow cannot be deleted."
                        ),
                        "code": "workflow.delete.protected",
                    }
                },
            )
        )
        result = runner.invoke(
            app,
            ["workflow", "delete", "wf-vibe", "--force"],
            env=env,
        )

    assert result.exit_code != 0
    assert isinstance(result.exception, APICallError)
    assert "managed Orcheo Vibe workflow cannot be deleted" in str(result.exception)


def test_workflow_delete_machine_mode_without_force(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Machine mode delete without --force prints JSON error and exits 1."""
    result = runner.invoke(
        app,
        ["workflow", "delete", "wf-1"],
        env=machine_env,
    )
    assert result.exit_code == 1
    assert '"error"' in result.stdout
    assert "--force" in result.stdout


def test_workflow_delete_machine_mode_with_force(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Machine mode delete with --force prints JSON result."""
    with respx.mock(assert_all_called=True) as router:
        router.delete("http://api.test/api/workflows/wf-1").mock(
            return_value=httpx.Response(204)
        )
        result = runner.invoke(
            app,
            ["workflow", "delete", "wf-1", "--force"],
            env=machine_env,
        )
    assert result.exit_code == 0
    assert "{" in result.stdout
