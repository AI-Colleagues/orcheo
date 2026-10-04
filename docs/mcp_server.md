# MCP Server

The Orcheo backend serves a [Model Context Protocol](https://modelcontextprotocol.io)
server alongside its REST API. Point an MCP client (Claude Code, Claude Desktop,
Cursor, or your own agent) at it to remote-control a running Orcheo server:
upload and inspect workflows, trigger runs and read their traces, manage
schedules, publishing, listeners and credentials.

The MCP server covers what you need **while the server is running**. Stack
lifecycle (`orcheo stack`, `orcheo install`) and plugin installation stay CLI-only.
Workspace administration is available through scoped tools. Service token
creation and revocation use a private MCP App so secrets never enter model context.

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

Tokens granted this way are only accepted at `/api/mcp`; every other API route
and WebSocket rejects them with `403` (`auth.oauth_client_token`). Clients cannot
approve their own consent requests or read credential secrets through REST.
Service token creation requires approved MCP access and a private App form;
its result exposes the new secret only to the embedded view.

The client only sees and can call the tools its approved scopes cover:

| Scope | Tools |
|-------|-------|
| `workflows:read` | Listing and reading workflows, versions, runs, traces, listeners and diagrams |
| `workflows:write` | Uploading, updating, deleting, publishing and scheduling workflows; saving configs; pausing and resuming listeners |
| `workflows:execute` | `run_workflow`, `cancel_run` |
| `vault:read` | `list_credentials`, `check_workflow_credentials` (with `workflows:read`) |
| `vault:write` | `open_credential_form`, `save_credential`, `delete_credential` |
| `workspaces:read` | Workspace administration metadata, members, invitations and audit events |
| `workspaces:write` | Workspace creation, lifecycle, membership and invitation changes |
| `admin:tokens:read` | Service token metadata, without secrets or secret previews |
| `admin:tokens:write` | Open private service token forms; App-only creation and revocation |

Credential health reads require both `workflows:read` and `vault:read`.
Validation requires `workflows:read` and `vault:write`; alert listing and
acknowledgement require `vault:read` and `vault:write`, respectively.
Evaluation requires both `workflows:execute` and `workflows:write`, because
evaluator definitions can load Python entrypoints. Node execution requires
`workflows:execute`. Webhook reads require `workflows:read`; webhook changes
and forms require `workflows:write`. Candidate installation and updates require
`workflows:write`, while catalog reads require `workflows:read`.

Default tokens and MCP client registrations retain workflow and vault access
and add `workspaces:read` and `admin:tokens:read`. The supported scopes also
include `workspaces:write` and `admin:tokens:write`, but clients must explicitly
request them when registering and obtain user consent. Studio and CLI
login/refresh tokens use the narrower defaults too. First-party operations
still follow the API's existing workspace authorization; service token creation
cannot delegate scopes absent from the caller's token.

Existing OAuth grants do not gain new scopes automatically. Reconnect and
approve the needed workspace or service-token access. Workspace roles still
apply; a grant does not turn an editor into an administrator.

The component catalog, workspace discovery (`list_my_workspaces` and
`get_active_workspace`) and server-info tools need no scope. Service
tokens and Studio sessions are not narrowed by these scopes; the API routes the
tools call authorize them as usual.

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

Workflow changes use the actor `mcp`. Workspace and service-token operations
retain the REST routes' audit behavior and authenticated caller identity.

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
| `update_workflow` | `workflow update` | Change name, handle, description, tags, draft access or ChatKit prompts/models. The two `clear_chatkit_*` flags remove stored overrides. |
| `delete_workflow` | `workflow delete` | Archive a workflow. |
| `publish_workflow` / `unpublish_workflow` | `workflow publish` / `unpublish` | Toggle public ChatKit access. |
| `schedule_workflow` / `unschedule_workflow` | `workflow schedule` / `unschedule` | Enable or disable the cron trigger declared by the latest version. |
| `save_workflow_config` | `workflow save-config` | Replace a version's stored runnable config. |
| `check_workflow_credentials` | — | Check that the credentials a workflow references exist. |
| `list_workflow_listeners` | — | List listener subscriptions with live health. |
| `pause_workflow_listener` / `resume_workflow_listener` | `workflow listeners pause` / `resume` | Control a listener subscription. |
| `diff_workflow_versions` | — | Compare two stored versions. |
| `get_workflow_listener_metrics` | — | Read aggregate health, stalled-listener alerts and dispatch failures. |
| `get_webhook_config` / `configure_webhook` | — | Inspect redacted webhook configuration or update supplied nonsecret settings while retaining authentication. |
| `open_webhook_form` | — | Let the user privately set webhook authentication and required header/query values. |

### Runs

| Tool | Description |
|------|-------------|
| `run_workflow` | Queue a run (latest or a pinned version), optionally override `runnable_config` for that run, and return immediately. Stored version configuration is unchanged. |
| `list_workflow_runs` | List the most recent runs of a workflow. |
| `get_run` | Show a run's status, inputs, output and error. |
| `get_run_trace` | Show node-level spans, timings and outputs. |
| `cancel_run` | Cancel a pending or running run. |
| `list_workflow_executions` | List recorded executions, including streaming and evaluation runs, without full step payloads. |
| `get_execution_history` | Read recorded inputs, configuration and steps. `from_step` slices the history; it does not re-execute the workflow. |
| `execute_node` | Execute an individual node with inputs and an optional workflow context. Integration nodes can have real external side effects. |
| `evaluate_workflow` | Evaluate a stored version using a dataset, evaluator definitions and optional runtime configuration. |
| `list_agentensor_checkpoints` / `get_agentensor_checkpoint` | Inspect saved optimization checkpoints. |

`run_workflow` does not wait for the run to finish. Poll `get_run` until the
status is terminal, then read `get_run_trace` for details.

`evaluate_workflow` waits for completion within `timeout_seconds` (default 60,
maximum 300). It returns an execution ID, status and result; full events are
stored in execution history. A timeout cancels the evaluation and returns an
error containing the execution ID. It is not a background queued run, and MCP
hosts must allow enough time for the call. Custom evaluator entrypoints require
client code uploads to be enabled. Evaluation uses the workspace's stored
workflow source; clients do not supply a separate graph.

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
| `get_workflow_credential_health` | Read cached credential validation results. |
| `validate_workflow_credentials` | Validate credentials with their providers and update health. Requires the credential health service. |
| `list_credential_alerts` / `acknowledge_credential_alert` | Inspect and acknowledge credential governance alerts. |
| `get_server_readiness` | Check backend readiness, including Redis when using queued execution. |
| `get_server_features` | Read features enabled for the selected workspace. |
| `list_server_plugins` | Inspect installed plugin versions and workspace availability. |

### Workspace administration

Scope approval does not grant a workspace role. Administrative routes still
require admin or owner membership. `workspace` selects the context authorizing
the call; lifecycle tools take a separate `workspace_id` identifying the
workspace being managed.
Member and invitation tools operate on the selected workspace.

| Tool | Description |
|------|-------------|
| `list_workspaces` / `get_workspace` | Admin workspace inventory and metadata, including quotas and lifecycle state. |
| `create_workspace` | Create a workspace owned by the caller; supplying `owner_user_id` uses the admin creation route. Accepts optional quotas. |
| `update_workspace_status` | Activate, suspend or soft-delete a workspace. |
| `delete_workspace` | Permanently delete a workspace and its memberships. |
| `purge_deleted_workspaces` | Permanently delete soft-deleted workspaces past `retention_days` (default 30). |
| `list_workspace_audit_events` | Read up to 500 workspace audit events. |
| `list_workspace_members` / `add_workspace_member` | Inspect members or add a known user. |
| `update_workspace_member_role` / `remove_workspace_member` | Change roles or remove members; requires admin or owner role. |
| `list_workspace_invitations` / `create_workspace_invitation` / `revoke_workspace_invitation` | Inspect, email or revoke invitations. Acceptance links are delivered by email and never returned by these tools. |

### Service tokens

| Tool | Description |
|------|-------------|
| `list_service_tokens` / `get_service_token` | Read workspace token metadata, usage, expiry and revocation state. Neither secrets nor secret previews are returned. |
| `open_service_token_form` | Open a private form with `action="create"`, or `action="revoke"` plus an existing `token_id`. Can prefill a name, scopes and expiration for creation. |
| `create_service_token` / `revoke_service_token` | App-only actions requiring the signed form token; unavailable for direct model use. |

The user confirms creation or revocation in the App. Created tokens belong only
to the selected workspace, and their scopes cannot exceed the caller's approved
scopes. Revocation requires a reason and targets the ID bound to the form.
To replace a token, create and copy its replacement, update the consuming
application, then open a revoke form for the old token.

Token secrets are hashed in storage and cannot be recovered later. A new secret
is returned once, only in `_meta["orcheo/serviceTokenSecret"]`, which is private
to the App. Both normal `content` and `structuredContent` contain only safe
metadata. The view supports reveal, copy and clearing; it never forwards the
secret through model-context updates or saves it in browser storage. Form tokens
expire after 15 minutes and bind the caller, OAuth client, workspace identity,
action and target token. As with credential forms, they are stateless and may be
reused within that expiry; the view disables submission after success.

Hosts without MCP Apps or private result metadata cannot perform this flow;
use Studio or `orcheo token create` instead. Existing OAuth grants must be
reconnected to approve the new token-management scopes.

### Candidate workflows

| Tool | Description |
|------|-------------|
| `list_candidates` | Read the official candidate catalog and release notes. |
| `onboard_candidate` | Install a candidate into the selected workspace and optional team; re-onboarding appends a version. |
| `update_candidate_workflow` | Upgrade an installed candidate from the server's catalog while preserving user configuration. |

### Interactive views (MCP Apps)

Hosts that support [MCP Apps](https://modelcontextprotocol.io/extensions/apps)
render these tools' results as embedded views:

| Tool | View |
|------|------|
| `open_credential_form` | `ui://orcheo/credential-form`: a form where the user types the secret. |
| `open_webhook_form` | `ui://orcheo/webhook-form`: webhook authentication and required header/query values, entered directly by the user. |
| `open_service_token_form` | `ui://orcheo/service-token-form`: create, privately reveal/copy, or revoke a service token. |
| `show_workflow_diagram` | `ui://orcheo/workflow-diagram`: a version's graph rendered with Mermaid. In other hosts the model receives the Mermaid source instead. |

**Credentials never pass through the model.** The agent only opens the form. The
user types the secret, and the form saves it by calling `save_credential`, an
app-only tool (`visibility: ["app"]`) that requires a signed, 15-minute form
token. The host delivers that token to the view in the result's `_meta`, which
is never shown to the model. The agent is only told that the credential was
saved, so it can neither see a secret nor write one itself. Credentials cannot
be created from hosts without MCP Apps support; use Studio or
`orcheo credential create` instead.

Webhook authentication follows the same pattern. `save_webhook_config` is
app-only and requires a signed form token bound to the caller, workspace,
workflow and webhook purpose. Credential and webhook tokens cannot be used
interchangeably. Webhook reads and save results exclude secret values and
required header/query values. API validation errors are sanitized before they
reach the model. Non-App hosts can configure nonsecret webhook settings through
`configure_webhook`; authentication fields require the form or another trusted
administrative surface.

The diagram view loads Mermaid from `cdn.jsdelivr.net`; it declares that origin
in its content security policy. If the script cannot load, the view shows the
diagram source instead.
