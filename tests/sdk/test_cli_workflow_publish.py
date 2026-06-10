from __future__ import annotations
from datetime import timedelta
from pathlib import Path
import httpx
import pytest
import respx
from rich.console import Console
from typer.testing import CliRunner
from orcheo_sdk.cli.cache import CacheManager
from orcheo_sdk.cli.config import CLISettings
from orcheo_sdk.cli.errors import APICallError, CLIError
from orcheo_sdk.cli.main import app
from orcheo_sdk.cli.workflow.commands.publishing import (
    _apply_error_hints,
    _format_publish_timestamp,
    _print_publish_summary,
    _require_online,
    _update_workflow_cache,
    _visibility_label,
)


def _publish_response(require_login: bool = False) -> dict[str, object]:
    return {
        "workflow": {
            "id": "wf-1",
            "name": "Demo",
            "is_public": True,
            "require_login": require_login,
            "published_at": "2024-01-01T00:00:00Z",
        },
        "message": "Stored",
    }


def test_publish_workflow_success(runner: CliRunner, env: dict[str, str]) -> None:
    payload = _publish_response(require_login=True)
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows/wf-1/publish").mock(
            return_value=httpx.Response(201, json=payload)
        )
        result = runner.invoke(
            app,
            ["workflow", "publish", "wf-1", "--require-login", "--force"],
            env=env,
        )

    assert result.exit_code == 0
    assert "Workflow visibility updated successfully" in result.stdout
    assert "Require login: Yes" in result.stdout
    assert "http://api.test/chat/wf-1" in result.stdout


def test_publish_workflow_with_custom_public_base(
    runner: CliRunner, env: dict[str, str]
) -> None:
    payload = _publish_response()
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows/wf-1/publish").mock(
            return_value=httpx.Response(201, json=payload)
        )
        result = runner.invoke(
            app,
            [
                "workflow",
                "publish",
                "wf-1",
                "--force",
                "--studio-url",
                "https://studio.test",
            ],
            env=env,
        )

    assert result.exit_code == 0
    assert "https://studio.test/chat/wf-1" in result.stdout


def test_publish_workflow_not_found(runner: CliRunner, env: dict[str, str]) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows/missing/publish").mock(
            return_value=httpx.Response(
                404, json={"detail": {"message": "Workflow not found"}}
            )
        )
        result = runner.invoke(
            app,
            ["workflow", "publish", "missing", "--force"],
            env=env,
        )

    assert result.exit_code == 1
    assert isinstance(result.exception, APICallError)
    assert "Workflow 'missing' was not found" in str(result.exception)


def test_publish_workflow_forbidden(runner: CliRunner, env: dict[str, str]) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows/wf-1/publish").mock(
            return_value=httpx.Response(403, json={"detail": {"message": "Forbidden"}})
        )
        result = runner.invoke(
            app,
            ["workflow", "publish", "wf-1", "--force"],
            env=env,
        )

    assert result.exit_code == 1
    assert isinstance(result.exception, APICallError)
    assert "Permission denied when modifying workflow 'wf-1'" in str(result.exception)


def test_unpublish_workflow_success(runner: CliRunner, env: dict[str, str]) -> None:
    workflow = {
        "id": "wf-1",
        "name": "Demo",
        "is_public": False,
        "require_login": False,
    }
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows/wf-1/publish/revoke").mock(
            return_value=httpx.Response(200, json=workflow)
        )
        result = runner.invoke(
            app,
            ["workflow", "unpublish", "wf-1", "--force"],
            env=env,
        )

    assert result.exit_code == 0
    assert "Workflow is now private" in result.stdout
    assert "Share URL: -" in result.stdout


def test_update_workflow_cache_updates_entries(tmp_path: Path) -> None:
    cache = CacheManager(directory=tmp_path / "cache", ttl=timedelta(hours=1))
    workflow = {
        "id": "wf-1",
        "is_public": True,
        "share_url": "http://api.test/chat/wf-1",
        "is_archived": False,
    }
    cache.store(
        "workflows:archived:False",
        [{"id": "wf-1", "is_public": False, "share_url": None, "is_archived": False}],
    )

    _update_workflow_cache(cache, workflow)

    entry = cache.load("workflow:wf-1")
    assert entry is not None
    assert entry.payload["share_url"] == "http://api.test/chat/wf-1"

    list_entry = cache.load("workflows:archived:False")
    assert list_entry is not None
    payload = list_entry.payload
    assert isinstance(payload, list)
    assert payload[0]["share_url"] == "http://api.test/chat/wf-1"


def test_update_workflow_cache_removes_from_archived(tmp_path: Path) -> None:
    cache = CacheManager(directory=tmp_path / "cache2", ttl=timedelta(hours=1))
    workflow = {
        "id": "wf-archived",
        "is_public": True,
        "share_url": "http://api.test/chat/wf-archived",
        "is_archived": False,
    }
    cache.store(
        "workflows:archived:True",
        [{"id": "wf-archived", "is_public": True, "is_archived": True}],
    )

    _update_workflow_cache(cache, workflow)

    archived_entry = cache.load("workflows:archived:True")
    assert archived_entry is not None
    assert archived_entry.payload == []


def test_update_workflow_cache_appends_missing_entries(tmp_path: Path) -> None:
    cache = CacheManager(directory=tmp_path / "cache_append", ttl=timedelta(hours=1))
    workflow = {
        "id": "wf-new",
        "is_public": True,
        "share_url": "http://api.test/chat/wf-new",
        "is_archived": False,
    }
    cache.store(
        "workflows:archived:False",
        [
            {"id": "wf-existing", "is_public": False, "is_archived": False},
        ],
    )

    _update_workflow_cache(cache, workflow)

    entry = cache.load("workflows:archived:False")
    assert entry is not None
    payload = entry.payload
    assert isinstance(payload, list)
    assert payload[-1]["id"] == "wf-new"


def test_update_workflow_cache_skips_non_list_payloads(tmp_path: Path) -> None:
    cache = CacheManager(directory=tmp_path / "cache_non_list", ttl=timedelta(hours=1))
    workflow = {
        "id": "wf-raw",
        "is_public": True,
        "share_url": "http://api.test/chat/wf-raw",
        "is_archived": False,
    }
    cache.store("workflows:archived:False", {"id": "not-a-list"})

    _update_workflow_cache(cache, workflow)

    entry = cache.load("workflows:archived:False")
    assert entry is not None
    assert entry.payload == {"id": "not-a-list"}


def test_require_online_raises_when_offline() -> None:
    settings = CLISettings(
        api_url="http://api.test", service_token=None, profile=None, offline=True
    )
    with pytest.raises(CLIError) as exc:
        _require_online(settings)
    assert "This command requires network connectivity." in str(exc.value)


def test_apply_error_hints_handles_status_codes() -> None:
    missing = APICallError("oops", status_code=404)
    enforced = _apply_error_hints("wf-1", missing)
    assert "workflow list" in str(enforced).lower()
    assert enforced.status_code == 404

    forbidden = APICallError("no", status_code=403)
    enforced = _apply_error_hints("wf-1", forbidden)
    assert "workflow 'wf-1'" in str(enforced)
    assert enforced.status_code == 403

    other = APICallError("boom", status_code=500)
    assert _apply_error_hints("wf-1", other) is other


def test_visibility_label_and_timestamp_helpers() -> None:
    assert _visibility_label({"is_public": True}) == "Public"
    assert _visibility_label({"is_public": False}) == "Private"
    assert _format_publish_timestamp({"published_at": "2024-01-01T00:00:00Z"}) != "-"
    assert _format_publish_timestamp({}) == "-"


def test_print_publish_summary_displays_optional_fields() -> None:
    console = Console(record=True)
    workflow = {
        "is_public": False,
        "require_login": False,
        "published_at": "2024-01-01T00:00:00Z",
    }
    _print_publish_summary(
        console,
        workflow=workflow,
        share_url=None,
        message="Keep it secure.",
    )

    output = console.export_text()
    assert "Private" in output
    assert "Share URL: -" in output
    assert "Keep it secure." in output


def test_print_publish_summary_omits_message_when_absent() -> None:
    console = Console(record=True)
    workflow = {
        "is_public": True,
        "require_login": True,
        "published_at": "2024-03-01T00:00:00Z",
    }
    _print_publish_summary(
        console,
        workflow=workflow,
        share_url="http://api.test/chat/wf-omit",
        message=None,
    )

    output = console.export_text()
    assert "http://api.test/chat/wf-omit" in output
    assert "Hidden message" not in output


def test_update_workflow_cache_skips_when_id_missing(tmp_path: Path) -> None:
    cache = CacheManager(directory=tmp_path / "cache3", ttl=timedelta(hours=1))
    workflow = {"name": "no-id"}
    _update_workflow_cache(cache, workflow)
    assert not any(cache.directory.iterdir())


def test_publish_machine_mode_without_force(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Machine mode publish without --force prints JSON error and exits 1."""
    result = runner.invoke(
        app,
        ["workflow", "publish", "wf-1"],
        env=machine_env,
    )
    assert result.exit_code == 1
    assert '"error"' in result.stdout
    assert "--force" in result.stdout


def test_publish_machine_mode_with_force(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Machine mode publish with --force prints raw JSON result."""
    payload = _publish_response()
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows/wf-1/publish").mock(
            return_value=httpx.Response(201, json=payload)
        )
        result = runner.invoke(
            app,
            ["workflow", "publish", "wf-1", "--force"],
            env=machine_env,
        )
    assert result.exit_code == 0
    assert '"workflow"' in result.stdout


def test_unpublish_machine_mode_without_force(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Machine mode unpublish without --force prints JSON error and exits 1."""
    result = runner.invoke(
        app,
        ["workflow", "unpublish", "wf-1"],
        env=machine_env,
    )
    assert result.exit_code == 1
    assert '"error"' in result.stdout
    assert "--force" in result.stdout


def test_unpublish_machine_mode_with_force(
    runner: CliRunner, machine_env: dict[str, str]
) -> None:
    """Machine mode unpublish with --force prints raw JSON result."""
    workflow = {
        "id": "wf-1",
        "name": "Demo",
        "is_public": False,
        "require_login": False,
    }
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows/wf-1/publish/revoke").mock(
            return_value=httpx.Response(200, json=workflow)
        )
        result = runner.invoke(
            app,
            ["workflow", "unpublish", "wf-1", "--force"],
            env=machine_env,
        )
    assert result.exit_code == 0
    assert '"wf-1"' in result.stdout


def test_publish_command_prompts_for_confirmation(
    runner: CliRunner, env: dict[str, str]
) -> None:
    payload = _publish_response()
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows/wf-1/publish").mock(
            return_value=httpx.Response(201, json=payload)
        )
        result = runner.invoke(
            app,
            ["workflow", "publish", "wf-1"],
            input="y\n",
            env=env,
        )

    assert result.exit_code == 0
    assert "Publish workflow 'wf-1' as public?" in result.stdout


def test_unpublish_command_prompts_for_confirmation(
    runner: CliRunner, env: dict[str, str]
) -> None:
    workflow = {
        "id": "wf-1",
        "name": "Demo",
        "is_public": False,
        "require_login": False,
    }
    with respx.mock(assert_all_called=True) as router:
        router.post("http://api.test/api/workflows/wf-1/publish/revoke").mock(
            return_value=httpx.Response(200, json=workflow)
        )
        result = runner.invoke(
            app,
            ["workflow", "unpublish", "wf-1"],
            input="y\n",
            env=env,
        )

    assert result.exit_code == 0
    assert "Unpublish workflow 'wf-1'?" in result.stdout
