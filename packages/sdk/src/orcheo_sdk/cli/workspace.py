"""CLI commands for managing Orcheo workspaces."""

from __future__ import annotations
import os
from pathlib import Path
from typing import Annotated
import typer
from rich.table import Table
from orcheo_sdk.cli.output import print_json, print_machine_success, success
from orcheo_sdk.cli.state import CLIState
from orcheo_sdk.services.workspaces import (
    create_workspace_data,
    deactivate_workspace_data,
    delete_workspace_data,
    invite_workspace_member_data,
    list_my_workspaces_data,
    list_workspace_audit_events_data,
    list_workspaces_data,
    purge_deleted_workspaces_data,
    reactivate_workspace_data,
)


__all__ = ["workspace_app"]


workspace_app = typer.Typer(name="workspace", help="Manage Orcheo workspaces.")


_WORKSPACE_CONFIG_DIR = Path.home() / ".orcheo"
_WORKSPACE_CONFIG_FILE = _WORKSPACE_CONFIG_DIR / "workspace"


def _state(ctx: typer.Context) -> CLIState:
    return ctx.ensure_object(CLIState)


@workspace_app.command("create")
def create_workspace(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="URL-safe slug for the new workspace.")],
    name: Annotated[
        str, typer.Option("--name", help="Display name for the workspace.")
    ],
    owner: Annotated[
        str,
        typer.Option(
            "--owner",
            help="User identifier (subject) that becomes the workspace owner.",
        ),
    ],
) -> None:
    """Create a workspace with the given slug, name, and owner."""
    state = _state(ctx)
    data = create_workspace_data(
        state.client, slug=slug, name=name, owner_user_id=owner
    )
    if not state.human:
        print_json(data)
        return
    success(f"Workspace '{data['slug']}' created (id={data['id']})")
    state.console.print(f"[bold]Owner:[/] {owner}")


@workspace_app.command("list")
def list_workspaces(
    ctx: typer.Context,
    include_inactive: Annotated[
        bool,
        typer.Option(
            "--all/--active-only", help="Include suspended/deleted workspaces."
        ),
    ] = False,
) -> None:
    """List workspaces."""
    state = _state(ctx)
    data = list_workspaces_data(state.client, include_inactive=include_inactive)
    workspaces = data.get("workspaces", [])
    if not state.human:
        print_json(data)
        return
    table = Table(title=f"Workspaces ({len(workspaces)})")
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Status", style="green")
    table.add_column("ID", style="dim")
    for workspace in workspaces:
        table.add_row(
            workspace["slug"], workspace["name"], workspace["status"], workspace["id"]
        )
    state.console.print(table)


@workspace_app.command("deactivate")
def deactivate_workspace(
    ctx: typer.Context,
    workspace_id: Annotated[str, typer.Argument(help="Workspace identifier (UUID).")],
) -> None:
    """Mark a workspace as suspended."""
    state = _state(ctx)
    data = deactivate_workspace_data(state.client, workspace_id)
    if not state.human:
        print_json(data)
        return
    success(f"Workspace {data['slug']} suspended")


@workspace_app.command("reactivate")
def reactivate_workspace(
    ctx: typer.Context,
    workspace_id: Annotated[str, typer.Argument(help="Workspace identifier (UUID).")],
) -> None:
    """Reactivate a suspended workspace."""
    state = _state(ctx)
    data = reactivate_workspace_data(state.client, workspace_id)
    if not state.human:
        print_json(data)
        return
    success(f"Workspace {data['slug']} reactivated")


@workspace_app.command("delete")
def delete_workspace(
    ctx: typer.Context,
    workspace_id: Annotated[str, typer.Argument(help="Workspace identifier (UUID).")],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Delete without prompting for confirmation.",
        ),
    ] = False,
) -> None:
    """Hard-delete a workspace."""
    state = _state(ctx)
    if not force and state.human:
        confirmed = typer.confirm(
            f"Hard-delete workspace {workspace_id} and all memberships?"
        )
        if not confirmed:
            return
    data = delete_workspace_data(state.client, workspace_id)
    if not state.human:
        if data is None:
            print_machine_success(f"Workspace {workspace_id} deleted")
        else:
            print_json(data)
        return
    success(f"Workspace {workspace_id} deleted")


@workspace_app.command("purge-deleted")
def purge_deleted_workspaces(
    ctx: typer.Context,
    retention_days: Annotated[
        int,
        typer.Option(
            "--retention-days",
            help="Only purge workspaces deleted at least this many days ago.",
        ),
    ] = 30,
) -> None:
    """Purge deleted workspaces whose retention window has expired."""
    state = _state(ctx)
    data = purge_deleted_workspaces_data(
        state.client,
        retention_days=retention_days,
    )
    if not state.human:
        if data is None:
            print_machine_success(
                f"Purged deleted workspaces older than {retention_days} day(s)"
            )
        else:
            print_json(data)
        return
    success(f"Purged deleted workspaces older than {retention_days} day(s)")


@workspace_app.command("invite")
def invite_member(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="Workspace slug.")],
    user_id: Annotated[
        str, typer.Option("--user", help="Subject id for the new member.")
    ],
    role: Annotated[
        str,
        typer.Option("--role", help="Role: owner, admin, editor, or viewer."),
    ] = "editor",
) -> None:
    """Invite a member into a workspace."""
    state = _state(ctx)
    data = invite_workspace_member_data(
        state.client, slug=slug, user_id=user_id, role=role
    )
    if not state.human:
        print_json(data)
        return
    success(f"Added {user_id} as {role} in {slug}")


@workspace_app.command("audit-log")
def audit_log(
    ctx: typer.Context,
    workspace_id: Annotated[str, typer.Argument(help="Workspace identifier (UUID).")],
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum events to show."),
    ] = 100,
) -> None:
    """Show the most recent audit events for a workspace."""
    state = _state(ctx)
    data = list_workspace_audit_events_data(state.client, workspace_id, limit=limit)
    events = data.get("audit_events", [])
    if not state.human:
        print_json(data)
        return
    table = Table(title=f"Workspace audit events ({len(events)})")
    table.add_column("Action", style="cyan")
    table.add_column("Actor")
    table.add_column("Subject")
    table.add_column("Resource")
    table.add_column("Created At")
    for event in events:
        resource = event.get("resource_type") or ""
        resource_id = event.get("resource_id") or ""
        table.add_row(
            event.get("action", ""),
            event.get("actor") or "",
            event.get("subject") or "",
            f"{resource}:{resource_id}" if resource_id else resource,
            event.get("created_at", ""),
        )
    state.console.print(table)


@workspace_app.command("use")
def use_workspace(
    ctx: typer.Context,
    slug: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Workspace slug to use as the active workspace for "
                "subsequent CLI calls. "
                "Pass `--clear` to remove the override."
            )
        ),
    ] = None,
    clear: Annotated[
        bool,
        typer.Option("--clear", help="Remove the active workspace selection."),
    ] = False,
) -> None:
    """Show or set the active workspace slug for this CLI profile.

    The selection is written to ``~/.orcheo/workspace`` and exposed via the
    ``ORCHEO_WORKSPACE`` environment variable for child processes. The backend
    reads ``X-Orcheo-Workspace`` from the request, which the CLI populates from
    the active workspace selection.
    """
    state = _state(ctx)
    if clear:
        if _WORKSPACE_CONFIG_FILE.exists():
            _WORKSPACE_CONFIG_FILE.unlink()
        if not state.human:
            print_machine_success("Active workspace cleared")
            return
        success("Active workspace cleared")
        return

    if slug is None:
        current = _read_active_workspace()
        if current is None:
            current = os.environ.get("ORCHEO_WORKSPACE")
        if not state.human:
            print_json({"workspace": current})
            return
        if current:
            state.console.print(f"Active workspace: [bold cyan]{current}[/]")
        else:
            state.console.print("[dim]No active workspace configured.[/]")
        return

    _WORKSPACE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _WORKSPACE_CONFIG_FILE.write_text(f"{slug}\n", encoding="utf-8")
    if not state.human:
        print_machine_success(f"Active workspace set to {slug}")
        return
    success(f"Active workspace set to '{slug}'")


@workspace_app.command("me")
def list_my_memberships(ctx: typer.Context) -> None:
    """Show workspaces the calling principal belongs to."""
    state = _state(ctx)
    data = list_my_workspaces_data(state.client)
    if not state.human:
        print_json(data)
        return
    memberships = data.get("memberships", [])
    table = Table(title="Your workspaces")
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Role", style="green")
    table.add_column("Status")
    for entry in memberships:
        table.add_row(entry["slug"], entry["name"], entry["role"], entry["status"])
    state.console.print(table)


def _read_active_workspace() -> str | None:
    if not _WORKSPACE_CONFIG_FILE.exists():
        return None
    try:
        candidate = _WORKSPACE_CONFIG_FILE.read_text(encoding="utf-8").strip()
    except OSError:  # pragma: no cover - defensive
        return None
    return candidate or None
