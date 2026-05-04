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
    invite_tenant_member_data,
    list_my_tenants_data,
    list_tenants_data,
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
