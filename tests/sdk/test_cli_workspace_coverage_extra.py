"""Extra coverage for workspace CLI commands."""

from __future__ import annotations
import io
from pathlib import Path
from unittest.mock import MagicMock
import click
import pytest
import typer
from rich.console import Console
from orcheo_sdk.cli import workspace as workspace_mod
from orcheo_sdk.cli.config import CLISettings
from orcheo_sdk.cli.state import CLIState


def _make_ctx(*, human: bool) -> tuple[typer.Context, CLIState, io.StringIO]:
    buffer = io.StringIO()
    state = CLIState(
        settings=CLISettings(
            api_url="http://api.test", service_token=None, profile="p"
        ),
        client=MagicMock(),
        cache=MagicMock(),
        console=Console(
            file=buffer, force_terminal=False, color_system=None, width=120
        ),
        human=human,
    )
    ctx = typer.Context(click.Command("workspace"))
    ctx.obj = state
    return ctx, state, buffer


def test_create_workspace_human_and_machine_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create should emit human and machine outputs depending on mode."""

    monkeypatch.setattr(
        workspace_mod,
        "create_workspace_data",
        lambda client, *, slug, name, owner_user_id: {
            "id": "ws-1",
            "slug": slug,
            "name": name,
            "owner_user_id": owner_user_id,
        },
    )
    success_messages: list[str] = []
    monkeypatch.setattr(
        workspace_mod,
        "success",
        lambda message: success_messages.append(message),
    )

    ctx, state, _ = _make_ctx(human=True)
    printed: list[str] = []
    state.console.print = lambda *args, **kwargs: printed.append(str(args[0]))  # type: ignore[assignment]
    workspace_mod.create_workspace(ctx, "acme", name="Acme", owner="alice")
    assert any("Workspace 'acme' created" in text for text in success_messages)
    assert any("Owner:" in text for text in printed)

    ctx, state, _ = _make_ctx(human=False)
    machine: list[object] = []
    monkeypatch.setattr(workspace_mod, "print_json", lambda data: machine.append(data))
    workspace_mod.create_workspace(ctx, "globex", name="Globex", owner="bob")
    assert machine[0]["slug"] == "globex"


def test_list_workspaces_human_and_machine_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list should render a table in human mode and JSON in machine mode."""

    payload = {
        "workspaces": [
            {"slug": "acme", "name": "Acme", "status": "active", "id": "ws-1"}
        ]
    }
    monkeypatch.setattr(
        workspace_mod,
        "list_workspaces_data",
        lambda client, *, include_inactive: payload,
    )

    ctx, state, buffer = _make_ctx(human=True)
    workspace_mod.list_workspaces(ctx, include_inactive=False)
    rendered = buffer.getvalue()
    assert "Workspaces (1)" in rendered
    assert "acme" in rendered

    ctx, state, _ = _make_ctx(human=False)
    machine: list[object] = []
    monkeypatch.setattr(workspace_mod, "print_json", lambda data: machine.append(data))
    workspace_mod.list_workspaces(ctx, include_inactive=True)
    assert machine[0]["workspaces"][0]["slug"] == "acme"


def test_deactivate_and_reactivate_machine_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deactivate/reactivate should return JSON in machine mode."""

    monkeypatch.setattr(
        workspace_mod,
        "deactivate_workspace_data",
        lambda client, workspace_id: {"slug": "acme", "status": "suspended"},
    )
    monkeypatch.setattr(
        workspace_mod,
        "reactivate_workspace_data",
        lambda client, workspace_id: {"slug": "acme", "status": "active"},
    )
    machine: list[object] = []
    monkeypatch.setattr(workspace_mod, "print_json", lambda data: machine.append(data))

    ctx, _, _ = _make_ctx(human=False)
    workspace_mod.deactivate_workspace(ctx, "ws-1")
    workspace_mod.reactivate_workspace(ctx, "ws-1")

    assert [item["status"] for item in machine] == ["suspended", "active"]


def test_delete_workspace_confirmation_and_machine_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete should short-circuit on confirmation and print machine success."""

    ctx, state, _ = _make_ctx(human=True)
    monkeypatch.setattr(typer, "confirm", lambda *args, **kwargs: False)
    deleted: list[str] = []
    monkeypatch.setattr(
        workspace_mod,
        "delete_workspace_data",
        lambda *args, **kwargs: deleted.append("called"),
    )
    workspace_mod.delete_workspace(ctx, "ws-1", force=False)
    assert deleted == []

    ctx, state, _ = _make_ctx(human=False)
    monkeypatch.setattr(
        workspace_mod,
        "delete_workspace_data",
        lambda client, workspace_id: None,
    )
    machine: list[str] = []
    monkeypatch.setattr(
        workspace_mod,
        "print_machine_success",
        lambda message: machine.append(message),
    )
    workspace_mod.delete_workspace(ctx, "ws-1", force=True)
    assert machine == ["Workspace ws-1 deleted"]


def test_use_workspace_set_show_and_clear(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """use should set, show, and clear the active workspace selection."""

    config_file = tmp_path / "workspace"
    monkeypatch.setattr(workspace_mod, "_WORKSPACE_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(workspace_mod, "_WORKSPACE_CONFIG_FILE", config_file)

    ctx, _, _ = _make_ctx(human=False)
    machine: list[object] = []
    monkeypatch.setattr(
        workspace_mod, "print_machine_success", lambda message: machine.append(message)
    )
    monkeypatch.setattr(workspace_mod, "print_json", lambda data: machine.append(data))

    workspace_mod.use_workspace(ctx, slug="acme", clear=False)
    assert config_file.read_text(encoding="utf-8") == "acme\n"
    assert machine[-1] == "Active workspace set to acme"

    workspace_mod.use_workspace(ctx, slug=None, clear=False)
    assert machine[-1] == {"workspace": "acme"}

    workspace_mod.use_workspace(ctx, slug=None, clear=True)
    assert machine[-1] == "Active workspace cleared"


def test_use_workspace_reads_env_and_handles_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """use should fall back to env vars and tolerate missing files."""

    config_file = tmp_path / "workspace"

    class BrokenFile:
        def exists(self) -> bool:
            return True

        def read_text(self, encoding: str = "utf-8") -> str:
            del encoding
            raise OSError("boom")

    monkeypatch.setattr(workspace_mod, "_WORKSPACE_CONFIG_FILE", BrokenFile())
    assert workspace_mod._read_active_workspace() is None

    monkeypatch.setattr(workspace_mod, "_WORKSPACE_CONFIG_FILE", config_file)
    monkeypatch.setenv("ORCHEO_WORKSPACE", "workspace-from-env")
    ctx, _, _ = _make_ctx(human=True)
    printed: list[str] = []
    ctx.obj.console.print = lambda *args, **kwargs: printed.append(str(args[0]))  # type: ignore[assignment]
    workspace_mod.use_workspace(ctx, slug=None, clear=False)
    assert any("workspace-from-env" in text for text in printed)


def test_use_workspace_human_set_and_missing_current_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Human mode should print both the set message and the empty-state message."""

    config_file = tmp_path / "workspace"
    monkeypatch.setattr(workspace_mod, "_WORKSPACE_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(workspace_mod, "_WORKSPACE_CONFIG_FILE", config_file)
    monkeypatch.delenv("ORCHEO_WORKSPACE", raising=False)

    ctx, _, _ = _make_ctx(human=True)
    printed: list[str] = []
    ctx.obj.console.print = lambda *args, **kwargs: printed.append(str(args[0]))  # type: ignore[assignment]
    success_messages: list[str] = []
    monkeypatch.setattr(
        workspace_mod, "success", lambda message: success_messages.append(message)
    )

    workspace_mod.use_workspace(ctx, slug="acme", clear=False)
    workspace_mod.use_workspace(ctx, slug=None, clear=False)
    config_file.unlink()
    workspace_mod.use_workspace(ctx, slug=None, clear=False)

    assert any("Active workspace set to 'acme'" in text for text in success_messages)
    assert any("Active workspace: " in text for text in printed)
    assert any("No active workspace configured" in text for text in printed)


def test_invite_and_membership_listing_machine_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invite and membership listing should emit JSON in machine mode."""

    monkeypatch.setattr(
        workspace_mod,
        "invite_workspace_member_data",
        lambda client, *, slug, user_id, role: {
            "slug": slug,
            "user_id": user_id,
            "role": role,
        },
    )
    monkeypatch.setattr(
        workspace_mod,
        "list_my_workspaces_data",
        lambda client: {
            "memberships": [
                {"slug": "acme", "name": "Acme", "role": "owner", "status": "active"}
            ]
        },
    )
    machine: list[object] = []
    monkeypatch.setattr(workspace_mod, "print_json", lambda data: machine.append(data))

    ctx, _, _ = _make_ctx(human=False)
    workspace_mod.invite_member(ctx, "acme", user_id="bob", role="viewer")
    workspace_mod.list_my_memberships(ctx)
    assert machine[0]["role"] == "viewer"
    assert machine[1]["memberships"][0]["slug"] == "acme"


def test_audit_log_and_purge_human_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit and purge commands should render human output paths."""

    monkeypatch.setattr(
        workspace_mod,
        "list_workspace_audit_events_data",
        lambda client, workspace_id, limit: {
            "audit_events": [
                {
                    "action": "created",
                    "actor": "alice",
                    "subject": "workspace",
                    "resource_type": "workspace",
                    "resource_id": workspace_id,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
    )
    monkeypatch.setattr(
        workspace_mod,
        "purge_deleted_workspaces_data",
        lambda client, retention_days: None,
    )
    success_messages: list[str] = []
    monkeypatch.setattr(
        workspace_mod, "success", lambda message: success_messages.append(message)
    )

    ctx, _, buffer = _make_ctx(human=True)
    workspace_mod.audit_log(ctx, "ws-1", limit=5)
    workspace_mod.purge_deleted_workspaces(ctx, retention_days=7)

    rendered = buffer.getvalue()
    assert "Workspace audit events (1)" in rendered
    assert "Purged deleted workspaces older than 7 day(s)" in success_messages[0]
