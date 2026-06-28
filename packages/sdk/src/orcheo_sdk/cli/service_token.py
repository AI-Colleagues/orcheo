"""CLI commands for managing service tokens."""

from __future__ import annotations
from typing import Annotated
import typer
from rich.table import Table
from orcheo_sdk.cli.output import (
    format_datetime,
    print_json,
    print_machine_success,
    print_markdown_table,
    success,
    warning,
)
from orcheo_sdk.cli.state import CLIState
from orcheo_sdk.services.service_tokens import (
    create_service_token_data,
    list_service_tokens_data,
    revoke_service_token_data,
    show_service_token_data,
)


app = typer.Typer(name="token", help="Manage service tokens for authentication")


def _state(ctx: typer.Context) -> CLIState:
    return ctx.ensure_object(CLIState)


@app.command("create")
def create_token(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Optional human-readable name for the token (need not be unique)",
        ),
    ] = None,
    scopes: Annotated[
        list[str] | None,
        typer.Option(
            "--scope", "-s", help="Scopes to grant (can be specified multiple times)"
        ),
    ] = None,
    workspaces: Annotated[
        list[str] | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace IDs the token can access (can be specified multiple times)",
        ),
    ] = None,
    expires_in: Annotated[
        int | None,
        typer.Option(
            "--expires-in",
            help="Expiration time in seconds (no expiration if omitted)",
            min=60,
        ),
    ] = None,
) -> None:
    """Create a new service token.

    The token secret is displayed once and cannot be retrieved later.
    Store it securely.
    """
    state = _state(ctx)

    data = create_service_token_data(
        state.client,
        name=name,
        scopes=scopes,
        workspace_ids=workspaces,
        expires_in_seconds=expires_in,
    )

    if not state.human:
        print_json(data)
        return

    state.console.print()
    state.console.print(
        "[bold green]Service token created successfully![/]",
        style="green",
    )
    state.console.print()
    if data.get("name"):
        state.console.print(f"[bold]Name:[/] {data['name']}")
    state.console.print(f"[bold]ID:[/] {data['identifier']}")
    state.console.print(
        f"[bold yellow]Secret:[/] [reverse]{data['secret']}[/]",
        style="yellow",
    )
    state.console.print()
    warning("Store this secret securely. It will not be shown again.")
    state.console.print()

    if data.get("scopes"):
        state.console.print(f"[bold]Scopes:[/] {', '.join(data['scopes'])}")
    if data.get("workspace_ids"):
        state.console.print(f"[bold]Workspaces:[/] {', '.join(data['workspace_ids'])}")
    if data.get("expires_at"):
        state.console.print(f"[bold]Expires:[/] {format_datetime(data['expires_at'])}")


@app.command("list")
def list_tokens(ctx: typer.Context) -> None:
    """List all service tokens."""
    state = _state(ctx)

    data = list_service_tokens_data(state.client)

    tokens = data.get("tokens", [])

    if not state.human:
        print_markdown_table(tokens)
        return

    table = Table(title=f"Service Tokens ({data['total']} total)")
    table.add_column("Name", style="cyan")
    table.add_column("Scopes", style="green")
    table.add_column("Workspaces", style="blue")
    table.add_column("Issued", style="dim")
    table.add_column("Expires", style="yellow")
    table.add_column("Status", style="magenta")

    for token in tokens:
        scopes_str = ", ".join(token.get("scopes", [])) or "-"
        workspaces_str = ", ".join(token.get("workspace_ids", [])) or "-"
        issued_str = (
            format_datetime(token["issued_at"]) if token.get("issued_at") else "-"
        )
        expires_str = (
            format_datetime(token["expires_at"]) if token.get("expires_at") else "Never"
        )

        if token.get("revoked_at"):
            status = "[red]Revoked[/]"
        elif token.get("rotated_to"):
            status = "[yellow]Rotated[/]"
        else:
            status = "[green]Active[/]"

        table.add_row(
            token.get("name") or token["identifier"],
            scopes_str,
            workspaces_str,
            issued_str,
            expires_str,
            status,
        )

    state.console.print(table)


@app.command("show")
def show_token(
    ctx: typer.Context,
    token_id: str = typer.Argument(..., help="Token identifier"),
) -> None:
    """Show details for a specific service token."""
    state = _state(ctx)

    data = show_service_token_data(state.client, token_id)

    if not state.human:
        print_json(data)
        return

    state.console.print()
    state.console.print(f"[bold]Service Token: {data['identifier']}[/]")
    state.console.print()

    table = Table(show_header=False, box=None)
    table.add_column("Field", style="bold")
    table.add_column("Value")

    if data.get("name"):
        table.add_row("Name", data["name"])

    table.add_row("ID", data["identifier"])

    if data.get("scopes"):
        table.add_row("Scopes", ", ".join(data["scopes"]))
    else:
        table.add_row("Scopes", "[dim]-[/]")

    if data.get("workspace_ids"):
        table.add_row("Workspaces", ", ".join(data["workspace_ids"]))
    else:
        table.add_row("Workspaces", "[dim]-[/]")

    if data.get("issued_at"):
        table.add_row("Issued", format_datetime(data["issued_at"]))

    if data.get("expires_at"):
        table.add_row("Expires", format_datetime(data["expires_at"]))
    else:
        table.add_row("Expires", "[dim]Never[/]")

    if data.get("revoked_at"):
        table.add_row(
            "Revoked",
            f"[red]{format_datetime(data['revoked_at'])}[/]",
        )
        if data.get("revocation_reason"):
            table.add_row("Reason", data["revocation_reason"])

    if data.get("rotated_to"):
        table.add_row("Rotated To", data["rotated_to"])

    state.console.print(table)
    state.console.print()


@app.command("revoke")
def revoke_token(
    ctx: typer.Context,
    token_id: str = typer.Argument(..., help="Token identifier to revoke"),
    reason: str = typer.Option(..., "--reason", "-r", help="Reason for revocation"),
) -> None:
    """Revoke a service token immediately."""
    state = _state(ctx)

    revoke_service_token_data(state.client, token_id, reason)

    if not state.human:
        print_machine_success(f"Service token '{token_id}' revoked successfully")
        return

    success(f"Service token '{token_id}' revoked successfully")
    state.console.print(f"[dim]Reason: {reason}[/]")


__all__ = ["app"]
