"""Edge-related CLI commands."""

from __future__ import annotations
from typing import Annotated
import typer
from orcheo_sdk.cli.output import (
    print_json,
    print_markdown_table,
    render_json,
    render_table,
)
from orcheo_sdk.cli.state import CLIState
from orcheo_sdk.services import list_edges_data, show_edge_data


edge_app = typer.Typer(help="Inspect available edges and their schemas.")

CategoryOption = Annotated[
    str | None,
    typer.Option("--category", help="Filter edges by category keyword."),
]
NameArgument = Annotated[
    str,
    typer.Argument(help="Edge name as registered in the catalog."),
]


def _state(ctx: typer.Context) -> CLIState:
    return ctx.ensure_object(CLIState)


@edge_app.command("list")
def list_edges(ctx: typer.Context, category: CategoryOption = None) -> None:
    """List registered edges with metadata."""
    state = _state(ctx)
    edges = list_edges_data(category=category)
    if not state.human:
        print_markdown_table(edges)
        return
    rows = [
        [
            item.get("name"),
            item.get("category"),
            item.get("description"),
        ]
        for item in edges
    ]
    render_table(
        state.console,
        title="Available Edges",
        columns=["Name", "Category", "Description"],
        rows=rows,
    )


@edge_app.command("show")
def show_edge(ctx: typer.Context, name: NameArgument) -> None:
    """Display metadata and schema information for ``name``."""
    state = _state(ctx)
    data = show_edge_data(name)
    if not state.human:
        print_json(data)
        return

    state.console.print(f"[bold]{data['name']}[/bold] ({data['category']})")
    state.console.print(data["description"])

    schema = data.get("schema")
    if schema is not None:
        render_json(state.console, schema, title="Pydantic schema")
        return

    attributes = data.get("attributes")
    if attributes:
        render_json(state.console, {"attributes": attributes})
    else:  # pragma: no cover - fallback when neither schema nor attributes present
        state.console.print("\n[dim]No schema information available[/dim]")
