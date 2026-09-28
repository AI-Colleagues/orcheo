# MCP Server

The Orcheo backend serves a [Model Context Protocol](https://modelcontextprotocol.io)
server alongside its REST API. Point an MCP client (Claude Code, Claude Desktop,
Cursor, or your own agent) at it to remote-control a running Orcheo server:
upload and inspect workflows, trigger runs and read their traces, manage
schedules, publishing, listeners and credentials.

The MCP server covers what you need **while the server is running**. Stack
lifecycle (`orcheo stack`, `orcheo install`), plugin installation, workspace
administration and service-token management stay CLI-only.

## Endpoint

| Property | Value |
|----------|-------|
| URL | `https://<your-orcheo-host>/api/mcp` |
| Transport | Streamable HTTP, stateless, JSON responses |
| Auth | OAuth 2.1 sign-in through Studio, or `Authorization: Bearer <service-token>` |
| Workspace | Optional `X-Orcheo-Workspace: <slug>` header, or the `workspace` argument on each tool |
| Toggle | `ORCHEO_MCP_ENABLED` (default `true`) |

The endpoint lives under `/api`, and because the server is stateless any
backend replica can answer any request; you do not need sticky sessions.

## Connecting a client

Register the server URL; that is all most clients need:

```bash
claude mcp add --transport http orcheo https://orcheo.example.com/api/mcp
```

For clients configured through JSON (Claude Desktop, Cursor, and similar):

```json
{
  "mcpServers": {
    "orcheo": {
      "type": "http",
      "url": "https://orcheo.example.com/api/mcp"
    }
  }
}
```

On first use the client opens your browser on Studio. Sign in with the code
emailed to you if you are not signed in already, then approve the client on
the consent screen. The client receives its own tokens and refreshes them
without asking again, until you sign out everywhere or the grant is revoked.

For a local backend started with `make dev-server`, use
`http://localhost:2025/api/mcp`. When authentication is disabled no sign-in
is needed.

### Service tokens (headless agents)

Agents without a browser can send a service token instead of signing in:

```bash
claude mcp add --transport http orcheo https://orcheo.example.com/api/mcp \
  --header "Authorization: Bearer $ORCHEO_SERVICE_TOKEN" \
  --header "X-Orcheo-Workspace: my-team"
```

## How sign-in works

The server is built with [FastMCP](https://gofastmcp.com). The backend is both
the MCP resource server and an OAuth 2.1 authorization server, implemented as
a FastMCP `OAuthProvider` on top of the first-party passwordless login:

1. An unauthenticated request to `/api/mcp` gets `401` with
   `WWW-Authenticate: Bearer resource_metadata="…"` pointing at the
   [RFC 9728](https://www.rfc-editor.org/rfc/rfc9728) metadata at
   `/.well-known/oauth-protected-resource/api/mcp`.
2. That names the authorization server `https://<host>/api/oauth`. The client
   reads its metadata at `/.well-known/oauth-authorization-server/api/oauth`
   (also served at `/.well-known/openid-configuration/api/oauth`) and
   registers itself through dynamic client registration (`/api/oauth/register`).
3. The authorize endpoint (`/api/oauth/authorize`, PKCE `S256` required) sends
   the browser to Studio's `/oauth/consent` page. The user signs in there with
   the emailed code if needed, then approves the client.
4. The client exchanges the single-use code at `/api/oauth/token` for an access
   token and a refresh token. Access tokens are the same short-lived tokens
   Studio uses, limited to the scopes the client was granted. Refresh tokens
   rotate on use, are bound to the client, and are revoked by
   `/api/oauth/revoke` or by logging out everywhere.

Tokens granted this way cannot read credential secrets through the API.

**Deployment requirements**

- `ORCHEO_AUTH_JWT_SECRET` must be set (it is whenever first-party login is
  enabled). Without it the OAuth endpoints return `404` and only service tokens
  work.
- The backend must be reached over HTTPS (plain HTTP works only on
  `localhost`), as OAuth issuers require. On other plain-HTTP origins, OAuth is
  unavailable, a warning is logged, and only service tokens work.
- `ORCHEO_STUDIO_URL` must point at Studio, where the consent page lives.
- Discovery documents are served at the origin root. The bundled Caddy proxy
  forwards `/.well-known/oauth-*` and `/.well-known/openid-configuration*` to the
  backend; other reverse proxies must do the same, as well as `/api/*`.
- The backend builds the URLs it advertises from `ORCHEO_PUBLIC_URL`. When that
  is unset, it uses `X-Forwarded-Proto`/`X-Forwarded-Host` behind a trusted
  proxy (`ORCHEO_TRUSTED_PROXY=true`), otherwise the request's own origin.
- Browser-based MCP clients also need their origin in
  `ORCHEO_CORS_ALLOW_ORIGINS`.

## Security model

The caller is authenticated once per MCP request at `/api/mcp`. Tool calls then
replay in-process against the backend's own `/api` routes, carrying the
already-authenticated identity (plus the `X-Orcheo-Workspace` header and client
address), so the token is not validated a second time. Workspace membership,
role checks, quotas and workflow-upload trust settings apply exactly as they do
for the CLI and Studio.

Changes made through MCP are recorded in audit trails with the actor `mcp`.

!!! warning "Uploading workflows executes code"
    `upload_workflow` ingests Python like `orcheo workflow upload` does, so it
    is subject to `ORCHEO_WORKFLOW_TRUST_MODE` and the workflow definition
    mode. Only give MCP access to principals you would trust with the CLI.

## Tools

Workflow arguments accept a workflow ID or handle. Most tools also accept an
optional `workspace` slug.

### Workflows

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `list_workflows` | `workflow list` | List workflows with publish and schedule status. |
| `get_workflow` | `workflow show` | Workflow details, one version's metadata, and the five latest runs. |
| `list_workflow_versions` | — | Version metadata for a workflow. |
| `download_workflow` | `workflow download` | Python source and runnable config of a version. |
| `upload_workflow` | `workflow upload` | Ingest a script as a new version. It creates the workflow when given a new handle or a name, and re-syncs an existing cron schedule. |
| `update_workflow` | `workflow update` | Change a workflow's name, handle or description. |
| `delete_workflow` | `workflow delete` | Archive a workflow. |
| `publish_workflow` / `unpublish_workflow` | `workflow publish` / `unpublish` | Toggle public ChatKit access. |
| `schedule_workflow` / `unschedule_workflow` | `workflow schedule` / `unschedule` | Enable or disable the cron trigger declared by the latest version. |
| `save_workflow_config` | `workflow save-config` | Replace a version's stored runnable config. |
| `check_workflow_credentials` | — | Check that the credentials a workflow references exist. |
| `list_workflow_listeners` | — | List listener subscriptions with live health. |
| `pause_workflow_listener` / `resume_workflow_listener` | `workflow listeners pause` / `resume` | Control a listener subscription. |

### Runs

| Tool | Description |
|------|-------------|
| `run_workflow` | Queue a run (latest or a pinned version) and return it immediately. |
| `list_workflow_runs` | List the most recent runs of a workflow. |
| `get_run` | Show a run's status, inputs, output and error. |
| `get_run_trace` | Show node-level spans, timings and outputs. |
| `cancel_run` | Cancel a pending or running run. |

`run_workflow` does not wait for the run to finish. Poll `get_run` until the
status is terminal, then read `get_run_trace` for details.

### Components

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `list_components` | `node list`, `edge list`, `agent-tool list` | List the nodes, edges or agent tools registered on the server, plugins included. Pass `kind` to choose which. |
| `describe_component` | `node show`, `edge show`, `agent-tool show` | Show a component's description and configuration schema. |

These tools describe the server's registry, which can differ from a local SDK
install when the server runs different plugins.

### Credentials, workspaces and server

| Tool | Description |
|------|-------------|
| `open_credential_form` | Show the user a form to add a credential or update one (`credential_id`). |
| `list_credentials` | List vault credentials (metadata only; secrets are never returned). |
| `delete_credential` | Delete a credential in the current workspace. |
| `list_my_workspaces` | List the caller's workspace memberships. |
| `get_active_workspace` | Show which workspace calls act in, and the caller's role there. |
| `get_server_info` | Show component versions and whether uploads are allowed. |

### Interactive views (MCP Apps)

Hosts that support [MCP Apps](https://modelcontextprotocol.io/extensions/apps)
render these tools' results as embedded views:

| Tool | View |
|------|------|
| `open_credential_form` | `ui://orcheo/credential-form`: a form where the user types the secret. |
| `show_workflow_diagram` | `ui://orcheo/workflow-diagram`: a version's graph rendered with Mermaid. In other hosts the model receives the Mermaid source instead. |

**Credentials never pass through the model.** The agent only opens the form. The
user types the secret, and the form saves it by calling `save_credential`, an
app-only tool (`visibility: ["app"]`) that requires a signed, 15-minute form
token. The host delivers that token to the view in the result's `_meta`, which
is never shown to the model. The agent is only told that the credential was
saved, so it can neither see a secret nor write one itself. Credentials cannot
be created from hosts without MCP Apps support; use Studio or
`orcheo credential create` instead.

The diagram view loads Mermaid from `cdn.jsdelivr.net`; it declares that origin
in its content security policy. If the script cannot load, the view shows the
diagram source instead.
