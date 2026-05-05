"""CLI commands for managing Orcheo tenants."""

from __future__ import annotations
import os
from pathlib import Path
from typing import Annotated
import typer
from rich.table import Table
from orcheo_sdk.cli.output import print_json, print_machine_success, success
from orcheo_sdk.cli.state import CLIState
from orcheo_sdk.services.tenants import (
    create_tenant_data,
    deactivate_tenant_data,
    delete_tenant_data,
    invite_tenant_member_data,
    list_my_tenants_data,
    list_tenant_audit_events_data,
    list_tenants_data,
    purge_deleted_tenants_data,
    reactivate_tenant_data,
)


__all__ = ["tenant_app"]


tenant_app = typer.Typer(name="tenant", help="Manage Orcheo tenants.")


_TENANT_CONFIG_DIR = Path.home() / ".orcheo"
_TENANT_CONFIG_FILE = _TENANT_CONFIG_DIR / "tenant"


def _state(ctx: typer.Context) -> CLIState:
    return ctx.ensure_object(CLIState)


@tenant_app.command("create")
def create_tenant(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="URL-safe slug for the new tenant.")],
    name: Annotated[str, typer.Option("--name", help="Display name for the tenant.")],
    owner: Annotated[
        str,
        typer.Option(
            "--owner",
            help="User identifier (subject) that becomes the tenant owner.",
        ),
    ],
) -> None:
    """Create a tenant with the given slug, name, and owner."""
    state = _state(ctx)
    data = create_tenant_data(state.client, slug=slug, name=name, owner_user_id=owner)
    if not state.human:
        print_json(data)
        return
    success(f"Tenant '{data['slug']}' created (id={data['id']})")
    state.console.print(f"[bold]Owner:[/] {owner}")


@tenant_app.command("list")
def list_tenants(
    ctx: typer.Context,
    include_inactive: Annotated[
        bool,
        typer.Option("--all/--active-only", help="Include suspended/deleted tenants."),
    ] = False,
) -> None:
    """List tenants."""
    state = _state(ctx)
    data = list_tenants_data(state.client, include_inactive=include_inactive)
    tenants = data.get("tenants", [])
    if not state.human:
        print_json(data)
        return
    table = Table(title=f"Tenants ({len(tenants)})")
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Status", style="green")
    table.add_column("ID", style="dim")
    for tenant in tenants:
        table.add_row(tenant["slug"], tenant["name"], tenant["status"], tenant["id"])
    state.console.print(table)


@tenant_app.command("deactivate")
def deactivate_tenant(
    ctx: typer.Context,
    tenant_id: Annotated[str, typer.Argument(help="Tenant identifier (UUID).")],
) -> None:
    """Mark a tenant as suspended."""
    state = _state(ctx)
    data = deactivate_tenant_data(state.client, tenant_id)
    if not state.human:
        print_json(data)
        return
    success(f"Tenant {data['slug']} suspended")


@tenant_app.command("reactivate")
def reactivate_tenant(
    ctx: typer.Context,
    tenant_id: Annotated[str, typer.Argument(help="Tenant identifier (UUID).")],
) -> None:
    """Reactivate a suspended tenant."""
    state = _state(ctx)
    data = reactivate_tenant_data(state.client, tenant_id)
    if not state.human:
        print_json(data)
        return
    success(f"Tenant {data['slug']} reactivated")


@tenant_app.command("delete")
def delete_tenant(
    ctx: typer.Context,
    tenant_id: Annotated[str, typer.Argument(help="Tenant identifier (UUID).")],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Delete without prompting for confirmation.",
        ),
    ] = False,
) -> None:
    """Hard-delete a tenant."""
    state = _state(ctx)
    if not force and state.human:
        confirmed = typer.confirm(
            f"Hard-delete tenant {tenant_id} and all memberships?"
        )
        if not confirmed:
            return
    data = delete_tenant_data(state.client, tenant_id)
    if not state.human:
        if data is None:
            print_machine_success(f"Tenant {tenant_id} deleted")
        else:
            print_json(data)
        return
    success(f"Tenant {tenant_id} deleted")


@tenant_app.command("purge-deleted")
def purge_deleted_tenants(
    ctx: typer.Context,
    retention_days: Annotated[
        int,
        typer.Option(
            "--retention-days",
            help="Only purge tenants deleted at least this many days ago.",
        ),
    ] = 30,
) -> None:
    """Purge deleted tenants whose retention window has expired."""
    state = _state(ctx)
    data = purge_deleted_tenants_data(
        state.client,
        retention_days=retention_days,
    )
    if not state.human:
        if data is None:
            print_machine_success(
                f"Purged deleted tenants older than {retention_days} day(s)"
            )
        else:
            print_json(data)
        return
    success(f"Purged deleted tenants older than {retention_days} day(s)")


@tenant_app.command("invite")
def invite_member(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="Tenant slug.")],
    user_id: Annotated[
        str, typer.Option("--user", help="Subject id for the new member.")
    ],
    role: Annotated[
        str,
        typer.Option("--role", help="Role: owner, admin, editor, or viewer."),
    ] = "editor",
) -> None:
    """Invite a member into a tenant."""
    state = _state(ctx)
    data = invite_tenant_member_data(
        state.client, slug=slug, user_id=user_id, role=role
    )
    if not state.human:
        print_json(data)
        return
    success(f"Added {user_id} as {role} in {slug}")


@tenant_app.command("audit-log")
def audit_log(
    ctx: typer.Context,
    tenant_id: Annotated[str, typer.Argument(help="Tenant identifier (UUID).")],
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum events to show."),
    ] = 100,
) -> None:
    """Show the most recent audit events for a tenant."""
    state = _state(ctx)
    data = list_tenant_audit_events_data(state.client, tenant_id, limit=limit)
    events = data.get("audit_events", [])
    if not state.human:
        print_json(data)
        return
    table = Table(title=f"Tenant audit events ({len(events)})")
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


@tenant_app.command("use")
def use_tenant(
    ctx: typer.Context,
    slug: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Tenant slug to use as the active tenant for subsequent CLI calls. "
                "Pass `--clear` to remove the override."
            )
        ),
    ] = None,
    clear: Annotated[
        bool,
        typer.Option("--clear", help="Remove the active tenant selection."),
    ] = False,
) -> None:
    """Show or set the active tenant slug for this CLI profile.

    The selection is written to ``~/.orcheo/tenant`` and exposed via the
    ``ORCHEO_TENANT`` environment variable for child processes. The backend
    reads ``X-Orcheo-Tenant`` from the request, which the CLI populates from
    the active tenant selection.
    """
    state = _state(ctx)
    if clear:
        if _TENANT_CONFIG_FILE.exists():
            _TENANT_CONFIG_FILE.unlink()
        if not state.human:
            print_machine_success("Active tenant cleared")
            return
        success("Active tenant cleared")
        return

    if slug is None:
        current = _read_active_tenant()
        if current is None:
            current = os.environ.get("ORCHEO_TENANT")
        if not state.human:
            print_json({"tenant": current})
            return
        if current:
            state.console.print(f"Active tenant: [bold cyan]{current}[/]")
        else:
            state.console.print("[dim]No active tenant configured.[/]")
        return

    _TENANT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _TENANT_CONFIG_FILE.write_text(f"{slug}\n", encoding="utf-8")
    if not state.human:
        print_machine_success(f"Active tenant set to {slug}")
        return
    success(f"Active tenant set to '{slug}'")


@tenant_app.command("me")
def list_my_memberships(ctx: typer.Context) -> None:
    """Show tenants the calling principal belongs to."""
    state = _state(ctx)
    data = list_my_tenants_data(state.client)
    if not state.human:
        print_json(data)
        return
    memberships = data.get("memberships", [])
    table = Table(title="Your tenants")
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Role", style="green")
    table.add_column("Status")
    for entry in memberships:
        table.add_row(entry["slug"], entry["name"], entry["role"], entry["status"])
    state.console.print(table)


def _read_active_tenant() -> str | None:
    if not _TENANT_CONFIG_FILE.exists():
        return None
    try:
        candidate = _TENANT_CONFIG_FILE.read_text(encoding="utf-8").strip()
    except OSError:  # pragma: no cover - defensive
        return None
    return candidate or None
