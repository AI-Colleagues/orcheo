# CLI Reference

Orcheo ships with a LangGraph-friendly CLI for node discovery, workflow inspection, credential management, and reference code generation.

## Getting Started

After installing the SDK, the CLI is available immediately:

```bash
orcheo --help
```

Check which version you have installed:

```bash
orcheo --version
```

## Global Options

| Flag | Environment Variable | Description |
|------|---------------------|-------------|
| `--version` | — | Show the installed CLI version and exit. |
| `--human` | `ORCHEO_HUMAN=1` | Use human-friendly Rich output (colored tables, panels). Without this flag, the CLI defaults to **machine-readable output** (JSON, Markdown tables) suitable for scripting and piping. |
| `--profile <name>` | `ORCHEO_PROFILE` | Select a named profile from `cli.toml`. |
| `--api-url <url>` | `ORCHEO_API_URL` | Override the backend API URL. |
| `--service-token <token>` | `ORCHEO_SERVICE_TOKEN` | Override the service token. |
| `--workspace <slug>` | — | Active workspace slug to send on API requests for this invocation (see `orcheo workspace use` for a persistent default). |
| `--offline` | — | Fall back to cached data when network calls fail. |
| `--cache-ttl <hours>` | — | Cache TTL in hours for offline data (default: 24). |
| `--no-update-check` | `ORCHEO_DISABLE_UPDATE_CHECK=1` | Skip startup update reminders for the current invocation. |

## Output Modes

By default the CLI produces **machine-readable output**: lists render as Markdown tables, detail views render as JSON, and errors are returned as JSON objects. This makes the CLI suitable for use in scripts, CI pipelines, and tool integrations.

To get human-friendly output with Rich formatting (colored tables, syntax highlighting, status panels), pass `--human` or set the `ORCHEO_HUMAN` environment variable to a truthy value (`1`, `true`, `yes`, `on`):

```bash
# Machine-readable (default)
orcheo workflow list

# Human-friendly
orcheo --human workflow list

# Or via environment variable
export ORCHEO_HUMAN=1
orcheo workflow list
```

## Shell Auto-Completion

Enable fast shell auto-completion for commands and options:

```bash
orcheo --install-completion
```

This installs completion for your current shell (bash, zsh, fish, or PowerShell). After installation, restart your shell or source your shell configuration file.

## Available Commands

| Command | Description |
|---------|-------------|
| `orcheo node list [--tag <tag>]` | List registered nodes with metadata (name, category, description). Filter by tag. |
| `orcheo node show <node>` | Display detailed node schema, inputs/outputs, and credential requirements. |
| `orcheo edge list [--category <category>]` | List registered edges with metadata (name, category, description). Filter by category. |
| `orcheo edge show <edge>` | Display detailed edge schema and conditional routing configuration. Canonical built-ins use the `*Edge` suffix. |
| `orcheo plugin list [--runtime auto\|local\|stack]` | List installed plugins with enabled state, status, version, exports, and source ref. |
| `orcheo plugin show <plugin> [--runtime auto\|local\|stack]` | Show plugin manifest, compatibility, entry points, and resolved install state. |
| `orcheo plugin install <ref> [--runtime auto\|local\|stack]` | Install a plugin from a package name, pinned package, local path, wheel, or Git URL. |
| `orcheo plugin update <plugin> [--runtime auto\|local\|stack]` | Rebuild and update one plugin from its stored source ref. |
| `orcheo plugin update --all [--runtime auto\|local\|stack]` | Rebuild and update all configured plugins. |
| `orcheo plugin uninstall <plugin> [--runtime auto\|local\|stack]` | Remove a plugin and rebuild the shared plugin environment. |
| `orcheo plugin enable <plugin> [--runtime auto\|local\|stack]` | Mark an installed plugin enabled so the runtime may load it. |
| `orcheo plugin disable <plugin> [--runtime auto\|local\|stack]` | Mark an installed plugin disabled without forgetting its install source. |
| `orcheo plugin doctor [--runtime auto\|local\|stack]` | Run non-destructive diagnostics against plugin state, compatibility, and importability. |
| `orcheo plugin init <name> [--author <name>] [--exports <list>] [--output-dir <dir>]` | Scaffold a new plugin package skeleton (pyproject, manifest, starter node, tests). |
| `orcheo agent-tool list [--category <category>]` | List available agent tools with metadata. Filter by category. |
| `orcheo agent-tool show <tool>` | Display detailed tool schema and parameter information. |
| `orcheo workflow list [--archived]` | List workflows with owner, last run, and status. |
| `orcheo workflow show <workflow> [--version <num>]` | Print workflow summary, publish status/details, Mermaid graph, and runs. Use `--version` to show a specific version instead of the latest. |
| `orcheo workflow run <workflow> [--inputs <json> \| --inputs-file <path>] [--config <json> \| --config-file <path>] [--verbose] [--stream/--no-stream]` | Trigger a workflow execution. Streaming is enabled by default. |
| `orcheo workflow evaluate <workflow> [--inputs <json> \| --inputs-file <path>] [--config <json> \| --config-file <path>] [--evaluation <json> \| --evaluation-file <path>] [--verbose] [--stream/--no-stream]` | Trigger a workflow evaluation run (requires streaming mode). |
| `orcheo workflow update <workflow> [--handle <handle>] [--description <text>] [--chatkit-prompts <json> \| --chatkit-prompts-file <path> \| --clear-chatkit-prompts] [--chatkit-models <json> \| --chatkit-models-file <path> \| --clear-chatkit-models]` | Update workflow metadata, including per-workflow public ChatKit starter prompts and supported models. |
| `orcheo workflow upload <file> [--id <ref>] [--entrypoint <name>] [--config <json> \| --config-file <path>]` | Upload a workflow from a Python LangGraph script (`.py`). The script may declare an optional `# /// orcheo ... # ///` frontmatter block whose values (including the display name) are used as defaults (CLI flags override). |
| `orcheo workflow save-config <workflow> [--version <num>] (--config <json> \| --config-file <path> \| --clear)` | Save version `runnable_config` without creating a new version. |
| `orcheo workflow download <workflow> [-o <file>] [--config-out <file>] [--version <num>]` | Download workflow source as Python only. Use `--config-out` to write stored runnable config JSON when present, and `--version` to download a specific version. |
| `orcheo workflow delete <workflow> [--force]` | Delete a workflow with confirmation safeguards. |
| `orcheo workflow schedule <workflow>` | Activate cron scheduling based on the workflow's cron trigger (no-op if none). |
| `orcheo workflow unschedule <workflow>` | Remove cron scheduling for the workflow. |
| `orcheo workflow publish <workflow> [--require-login/--no-require-login] [--force] [--studio-url <url>]` | Publish a workflow for public ChatKit access, optionally requiring OAuth login and overriding the share-link origin for that run. |
| `orcheo workflow unpublish <workflow>` | Revoke public access and invalidate existing share links. |
| `orcheo workflow listeners <subcommand>` | Inspect and control workflow listeners (see `orcheo workflow listeners --help`). |
| `orcheo credential list [--workflow-id <id>]` | List credentials with scopes, expiry, and health status. |
| `orcheo credential create <name> --provider <provider> --secret <secret>` | Create a new credential with guided prompts. `--secret` is required. |
| `orcheo credential update <credential> [--secret <secret>] [--metadata <json>]` | Update a credential when supported by the backend. |
| `orcheo credential delete <credential> [--force]` | Revoke a credential with confirmation safeguards. |
| `orcheo auth login [--no-browser] [--port <port>]` | Authenticate via browser-based OAuth flow. |
| `orcheo auth logout` | Clear stored OAuth tokens for the current profile. |
| `orcheo auth status` | Show current authentication status (OAuth or service token). |
| `orcheo token create [--name <name>] [--scope <scope>] [--workspace <id>] [--expires-in <seconds>]` | Create a service token for CLI/API authentication. The server assigns the token ID; `--name` is an optional label. |
| `orcheo token list` | List all service tokens with their scopes and status. |
| `orcheo token show <token-id>` | Show detailed information for a specific service token. |
| `orcheo token revoke <token-id> --reason <reason>` | Immediately invalidate a service token (`--reason` is required). |
| `orcheo workspace list` / `me` / `use [<slug>]` | List workspaces, show the caller's memberships, and show or set the active workspace slug for the profile. |
| `orcheo workspace create` / `invite` / `deactivate` / `reactivate` / `delete` / `purge-deleted` / `audit-log` | Administer workspaces: creation, member invitations, lifecycle, and audit events. |
| `orcheo stack [--start\|--stop\|--restart\|--ps\|--pull\|--logs\|--down]` | Run common Docker Compose actions for the local Orcheo stack directory. |
| `orcheo config [--profile <name>] [--api-url <url>] [--service-token <token>] [--studio-url <url>] [--env-file <path>]` | Write CLI profile settings to `cli.toml`. Supports OAuth options (see below). |
| `orcheo config list` | List all configured CLI profiles. |
| `orcheo code template [-o <file>] [--name <name>]` | Generate a minimal Python LangGraph workflow template file. |
| `orcheo code scaffold <workflow>` | Generate Python SDK code snippets to invoke an existing workflow. |
| `orcheo install [--yes] [--mode install\|upgrade] [--stack-version <version>\|--staging] [--auth-mode api-key\|oauth] [--chatkit-domain-key <key>]` | Guided Docker-stack setup/upgrade. `--staging` installs the newest published prerelease stack. |
| `orcheo install upgrade [--yes] [--stack-version <version>\|--staging] [--auth-mode api-key\|oauth] [--chatkit-domain-key <key>]` | Guided upgrade shortcut command. |
| `orcheo install ensure-stack-env` | Create or backfill a stack env file without running the full install flow. |
| `orcheo browser-aware` | Start the browser context bridge server. |
| `orcheo context` | Inspect browser context from Studio tabs. |

`orcheo install` syncs stack assets into `~/.orcheo/stack` (or
`ORCHEO_STACK_DIR`). Files are refreshed when upstream content differs.
Use `--stack-version` (or `ORCHEO_STACK_VERSION`) to pin stack assets to a
specific `stack-v*` tag. Use `--staging` to resolve the newest prerelease.
Both paths pin matching `ORCHEO_STACK_IMAGE` and `ORCHEO_STUDIO_IMAGE` values.
When startup is enabled (`--start-stack`), setup then runs Docker Compose
(Docker must be installed). Setup also prompts for
`VITE_ORCHEO_CHATKIT_DOMAIN_KEY`; you can skip and continue, but ChatKit UI
features stay disabled until the key is set.

## Edge Naming

Built-in branching edges use canonical names with an `Edge` suffix:

- `IfElseEdge`
- `SwitchEdge`
- `WhileEdge`

`orcheo edge list` emits these canonical names. New examples and custom
registrations should use the canonical `*Edge` names.

## Plugin Management

The plugin subsystem uses a shared managed virtual environment plus desired and
lock state files under `~/.orcheo/plugins/` by default:

```text
~/.orcheo/
  plugins/
    plugins.toml
    plugin-lock.toml
    venv/
    wheels/
    manifests/
```

Cached plugin downloads and metadata live under `~/.cache/orcheo/plugins/`.
Set `ORCHEO_PLUGIN_DIR` to relocate the runtime-managed plugin tree. The cache
root continues to follow `ORCHEO_CACHE_DIR`.

Supported plugin refs:

- package: `orcheo-plugin-acme`
- pinned package: `orcheo-plugin-acme==0.1.0`
- local path: `./plugins/orcheo-plugin-acme`
- wheel: `dist/orcheo_plugin_acme-0.1.0-py3-none-any.whl`
- git: `git+https://github.com/acme/orcheo-plugin-acme.git`

`orcheo plugin install`, `update`, `disable`, and `uninstall` classify changes
as silent hot reload, confirmation-required hot reload, or restart-required.
Listener and trigger plugins always surface restart guidance because they are
not hot-reloadable in v1.

`orcheo plugin doctor` checks the managed plugin venv, Python version,
importability of enabled plugins, manifest hash integrity, plugin API
compatibility, Orcheo version compatibility, disabled-plugin references, and
lock consistency. It exits `1` when any error-level diagnostic is present.

### Validation listener plugins

Two reference plugins exercise the listener-plugin contract end to end:

```bash
orcheo plugin install "git+https://github.com/AI-Colleagues/orcheo-plugin-wecom-listener.git"
orcheo plugin install "git+https://github.com/AI-Colleagues/orcheo-plugin-lark-listener.git"
```

They register:

- `WeComListenerPluginNode` + listener platform `wecom`
- `LarkListenerPluginNode` + listener platform `lark`

Both plugins are intended to be operated through the plugin CLI. Because they
export listeners, install, update, disable, and uninstall operations should be
treated as restart or reconcile events for affected runtime processes.

### Plugin troubleshooting

Common operator flows:

- Broken import on startup: run `orcheo plugin doctor`, then
  `orcheo plugin disable <name>` if you need to restore service health quickly.
- Compatibility failure: `orcheo plugin show <name>` and `orcheo plugin doctor`
  will surface plugin API or Orcheo version mismatches. Reinstall a compatible
  plugin version or upgrade the plugin package.
- Integrity drift: if `plugin doctor` reports manifest or lock inconsistencies,
  reinstall or update the affected plugin so the managed venv and lock state are
  rebuilt together.

## Workflow Commands

### Running Workflows

Pass workflow inputs inline with `--inputs` or from disk via `--inputs-file`. Use `--config` or `--config-file` to provide LangChain runnable configuration for execution (each pair is mutually exclusive).

```bash
# Run with inline inputs
orcheo workflow run my-workflow --inputs '{"query": "hello"}'

# Run with inputs from file
orcheo workflow run my-workflow --inputs-file inputs.json
```

Streaming options:

- `--stream` (default): stream node and trace updates in real time.
- `--no-stream`: disable streaming for `workflow run` and create the run via API response payload.
- `--verbose`: include full payload output for stream updates.

In machine-readable mode (default, without `--human`), `--verbose` emits JSON event lines, including a start event (`workflow.execution.start`) and raw WebSocket updates until terminal status.

### Evaluating Workflows

Use `workflow evaluate` to run Agentensor evaluation mode:

```bash
# Evaluate with inline payload
orcheo workflow evaluate my-workflow \
  --inputs '{"query": "hello"}' \
  --evaluation '{"dataset": {"name": "golden"}}'

# Evaluate with files
orcheo workflow evaluate my-workflow \
  --inputs-file inputs.json \
  --evaluation-file evaluation.json \
  --verbose
```

`workflow evaluate` uses streaming mode and surfaces start events as `workflow.evaluation.start` in machine-readable verbose output.

### Publishing Workflows

Published workflows remain accessible until you run `orcheo workflow unpublish <workflow>` or toggle the `--require-login` flag to gate public chats behind OAuth.

```bash
# Publish a workflow for public access
orcheo workflow publish my-workflow

# Publish with login required
orcheo workflow publish my-workflow --require-login

# Revoke public access
orcheo workflow unpublish my-workflow
```

### Workflow Configuration

Upload-time defaults can be stored on a workflow version with `orcheo workflow upload ... --config` or `--config-file`. You can update config later without creating a version via `orcheo workflow save-config ...`. Stored config is merged with per-run overrides (run config wins). `orcheo workflow download --config-out <file>` writes that stored runnable config as companion JSON when present. Avoid putting secrets in runnable config; use environment variables or credential vaults instead.

### Workflow Frontmatter

Workflow `.py` files may include an optional PEP 723-style metadata block. When present, the `orcheo workflow upload` command reads it as the source of truth for the workflow's name, target id/handle, companion config file, and entrypoint. Any explicit CLI flag overrides the matching frontmatter field.

```python
# /// orcheo
# name = "My Workflow"
# id = "wf-abc123"
# config = "./my-workflow.config.json"
# entrypoint = "build_graph"
# avatar = "avatar-07"
# subtitle = "AI Assistant"
# version = "1.3.0"
# notes = "Best used with uploaded research documents."
#
# [[updates]]
# version = "1.3.0"
# summary = "Adds source-quality checks before synthesis."
# migration = "Review prompt overrides if they assume every source is accepted."
# ///

from langgraph.graph import StateGraph

def build_graph():
    return StateGraph(dict)
```

Supported fields (all optional):

- `name` – display name used when creating or renaming the workflow.
- `id` (or `handle`) – workflow reference; when set, the upload updates this existing workflow instead of creating a new one.
- `config` – path to a companion JSON runnable config file. Relative paths resolve against the workflow file's directory.
- `entrypoint` – LangGraph entrypoint function/variable name.
- `avatar` – avatar id shown on the colleague's Candidates badge in Studio.
- `subtitle` – short role line shown on the Candidates badge.
- `notes` – free-form notes describing the workflow template.
- `metadata` – a `[metadata]` table of template compatibility info (e.g. `required_plugins`, `validated_provider_api`).
- `version` – candidate release version using strict `MAJOR.MINOR.PATCH` SemVer. Candidates without a version stay onboardable but do not show update availability.
- `updates` – `[[updates]]` entries with `version`, `summary`, and optional `migration` text. Entries are shown to Studio users when they update an onboarded candidate colleague.

The block is parsed as TOML; only the fields above are accepted. CLI flags (`--id`, `--config-file`, `--entrypoint`) always win over the frontmatter values; the display name comes from the frontmatter `name` — update it there and re-upload to rename a workflow.

Candidate authors should bump patch versions for compatible fixes, minor versions for compatible behavior or capability additions, and major versions when prompt contracts, required config, credentials, or operational behavior may require review. Write `migration` notes for any risky or manual step; keep `summary` short enough to fit in update hover text.

## Offline Mode

Pass `--offline` to reuse cached metadata when disconnected:

```bash
orcheo node list --offline
orcheo workflow show <workflow-id> --offline
```

## Configuration

The CLI reads configuration from (highest precedence first):

1. Command flags: `--api-url`, `--service-token`, `--profile`
2. Environment variables: `ORCHEO_API_URL`, `ORCHEO_SERVICE_TOKEN`
3. Config file: `~/.config/orcheo/cli.toml` (profiles for multiple environments)

### Writing Profiles with `orcheo config`

The `config` command writes profile settings to `cli.toml`, pulling values from flags, an `.env` file, or the current environment:

```bash
# Write a profile from flags
orcheo config --api-url https://api.example.com --service-token sk-...

# Write a named profile
orcheo config --profile staging --api-url https://staging.example.com

# Import settings from a .env file
orcheo config --env-file .env

# Write multiple profiles at once
orcheo config --profile dev --profile staging --env-file .env
```

The `config` command also accepts OAuth settings for browser-based authentication:

| Flag | Environment Variable | Config Key |
|------|---------------------|------------|
| `--auth-issuer` | `ORCHEO_AUTH_ISSUER` | `auth_issuer` |
| `--auth-client-id` | `ORCHEO_AUTH_CLIENT_ID` | `auth_client_id` |
| `--auth-scopes` | `ORCHEO_AUTH_SCOPES` | `auth_scopes` |
| `--auth-audience` | `ORCHEO_AUTH_AUDIENCE` | `auth_audience` |
| `--auth-organization` | `ORCHEO_AUTH_ORGANIZATION` | `auth_organization` |

Once written to a profile, these values are used by `orcheo auth login` and other commands without needing environment variables.

See [Environment Variables](environment_variables.md) for the complete configuration reference.
