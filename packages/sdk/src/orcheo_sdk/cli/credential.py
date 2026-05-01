"""Credential management commands for the CLI."""

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
from orcheo_sdk.services import (
    create_credential_data,
    delete_credential_data,
    list_credentials_data,
)


credential_app = typer.Typer(help="Manage credentials stored in the Orcheo vault.")

WorkflowIdOption = Annotated[
    str | None,
    typer.Option("--workflow-id", help="Filter by workflow identifier."),
]
ScopeOption = Annotated[
    list[str] | None,
    typer.Option(
        "--scope",
        help="Optional list of scopes.",
        show_default=False,
    ),
]
KindOption = Annotated[
    str,
    typer.Option("--kind", help="Credential kind."),
]
CredentialNameArgument = Annotated[
    str,
    typer.Argument(help="Credential name."),
]
CredentialIdArgument = Annotated[
    str,
    typer.Argument(help="Credential identifier."),
]


def _state(ctx: typer.Context) -> CLIState:
    return ctx.ensure_object(CLIState)


@credential_app.command("list")
def list_credentials(
    ctx: typer.Context,
    workflow_id: WorkflowIdOption = None,
) -> None:
    """List credentials visible to the caller."""
    state = _state(ctx)
    credentials = list_credentials_data(state.client, workflow_id=workflow_id)
    if not state.human:
        print_markdown_table(credentials)
        return
    rows = [
        [
            item.get("id"),
            item.get("name"),
            item.get("provider"),
            item.get("status"),
            item.get("access"),
        ]
        for item in credentials
    ]
    render_table(
        state.console,
        title="Credentials",
        columns=["ID", "Name", "Provider", "Status", "Access"],
        rows=rows,
        column_overflow={"ID": "fold"},
    )


@credential_app.command("create")
def create_credential(
    ctx: typer.Context,
    name: CredentialNameArgument,
    provider: Annotated[
        str, typer.Option("--provider", help="Credential provider identifier.")
    ],
    secret: Annotated[str, typer.Option("--secret", help="Credential secret value.")],
    actor: Annotated[
        str, typer.Option("--actor", help="Actor creating the credential.")
    ] = "cli",
    access: Annotated[
        str,
        typer.Option("--access", help="Access level: scoped/shared."),
    ] = "shared",
    workflow_id: WorkflowIdOption = None,
    scopes: ScopeOption = None,
    kind: KindOption = "secret",
) -> None:
    """Create a credential via the vault API."""
    state = _state(ctx)
    response = create_credential_data(
        state.client,
        name=name,
        provider=provider,
        secret=secret,
        actor=actor,
        access=access,
        workflow_id=workflow_id,
        scopes=scopes,
        kind=kind,
    )
    if not state.human:
        print_json(response)
        return
    render_json(state.console, response, title="Credential created")


@credential_app.command("delete")
def delete_credential(
    ctx: typer.Context,
    credential_id: CredentialIdArgument,
    workflow_id: WorkflowIdOption = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip confirmation prompt."),
    ] = False,
) -> None:
    """Delete a credential from the vault."""
    state = _state(ctx)
    if not state.human and not force:
        print_json({"error": "Use --force to confirm deletion in machine mode."})
        raise typer.Exit(code=1)
    if not force:
        typer.confirm(
            "Are you sure you want to delete this credential?",
            abort=True,
        )
    result = delete_credential_data(
        state.client,
        credential_id,
        workflow_id=workflow_id,
    )
    if not state.human:
        print_json(result)
        return
    state.console.print(result.get("message", "Credential deleted."))


@credential_app.command("update")
def update_credential(
    ctx: typer.Context,
    credential_id: CredentialIdArgument,
    secret: Annotated[
        str | None, typer.Option("--secret", help="New secret value.")
    ] = None,
    metadata: Annotated[
        str | None,
        typer.Option("--metadata", help="JSON payload with metadata overrides."),
    ] = None,
) -> None:
    """Update credential metadata when supported by the backend."""
    state = _state(ctx)
    if not state.human:
        print_json(
            {"error": "Credential updates are not yet supported by the backend API."}
        )
        raise typer.Exit(code=1)
    state.console.print(
        "Credential updates are not yet supported by the backend API. "
        "Rotate credentials via templates or recreate them."
    )
    raise typer.Exit(code=0)
