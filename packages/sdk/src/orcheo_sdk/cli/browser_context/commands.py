"""CLI commands for browser context bridge."""

from __future__ import annotations
import logging
from typing import Annotated
import httpx
import typer
from orcheo_sdk.cli.output import print_json, render_json, render_table
from orcheo_sdk.cli.state import CLIState


browser_aware_app = typer.Typer(help="Start the browser context bridge server.")
context_app = typer.Typer(help="Inspect browser context from Studio tabs.")

DEFAULT_PORT = 3333


def _state(ctx: typer.Context) -> CLIState:
    """Retrieve CLI state from Typer context."""
    return ctx.ensure_object(CLIState)


def _context_base_url(port: int = DEFAULT_PORT) -> str:
    return f"http://localhost:{port}"


@browser_aware_app.callback(invoke_without_command=True)
def browser_aware(
    ctx: typer.Context,
    port: Annotated[
        int,
        typer.Option("--port", help="Port for the context bridge HTTP server."),
    ] = DEFAULT_PORT,
) -> None:
    """Start a local HTTP server that receives context from Studio browser tabs."""
    if ctx.invoked_subcommand is not None:
        return  # pragma: no cover

    from orcheo_sdk.cli.browser_context.server import run_server

    state = _state(ctx)
    state.console.print(
        f"[cyan]Browser context server starting on localhost:{port}[/cyan]"
    )
    state.console.print("Press Ctrl+C to stop.\n")
    logging.basicConfig(level=logging.INFO)
    run_server(host="localhost", port=port)


@context_app.callback(invoke_without_command=True)
def context(
    ctx: typer.Context,
    port: Annotated[
        int,
        typer.Option("--port", help="Port of the browser-aware HTTP server."),
    ] = DEFAULT_PORT,
) -> None:
    """Show the active browser context (current Studio page and workflow)."""
    if ctx.invoked_subcommand is not None:
        return

    state = _state(ctx)
    url = f"{_context_base_url(port)}/context"
    try:
        response = httpx.get(url, timeout=5)
        data = response.json()
    except httpx.ConnectError:
        msg = (
            "Could not connect to browser-aware server. "
            f"Run 'orcheo browser-aware' to start it (port {port})."
        )
        if state.human:
            state.console.print(f"[yellow]{msg}[/yellow]")
        else:
            print_json({"error": msg})
        raise typer.Exit(code=1) from None

    if data.get("total_sessions", 0) == 0:
        msg = (
            "No active Studio session found. "
            "Open Orcheo Studio in your browser to provide context."
        )
        if state.human:
            state.console.print(f"[yellow]{msg}[/yellow]")
        else:
            print_json({"warning": msg, **data})
        return

    if state.human:
        render_json(state.console, data, title="Active Context")
    else:
        print_json(data)


@context_app.command("sessions")
def context_sessions(
    ctx: typer.Context,
    port: Annotated[
        int,
        typer.Option("--port", help="Port of the browser-aware HTTP server."),
    ] = DEFAULT_PORT,
) -> None:
    """List all active Studio sessions."""
    state = _state(ctx)
    url = f"{_context_base_url(port)}/context/sessions"
    try:
        response = httpx.get(url, timeout=5)
        data = response.json()
    except httpx.ConnectError:
        msg = (
            "Could not connect to browser-aware server. "
            f"Run 'orcheo browser-aware' to start it (port {port})."
        )
        if state.human:
            state.console.print(f"[yellow]{msg}[/yellow]")
        else:
            print_json({"error": msg})
        raise typer.Exit(code=1) from None

    if not data:
        msg = "No active sessions."
        if state.human:
            state.console.print(f"[yellow]{msg}[/yellow]")
        else:
            print_json([])
        return

    if state.human:
        columns = [
            "session_id",
            "page",
            "workflow_id",
            "workflow_name",
            "focused",
            "staleness_seconds",
            "origin",
        ]
        rows = [[session.get(col, "") for col in columns] for session in data]
        render_table(
            state.console,
            title="Active Sessions",
            columns=columns,
            rows=rows,
        )
    else:
        print_json(data)
