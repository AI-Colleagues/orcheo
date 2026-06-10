# Manual Setup Guide

This guide covers manual installation and configuration of Orcheo for users who prefer direct control over their setup.

## Quick Start

For one-line installation, see the [Quick Start section on the landing page](index.md#quick-start).
If you already have the SDK installed, run `orcheo install` to set up or upgrade the stack.

## Prerequisites

- **Docker**: Required for running Redis and other services via Docker Compose
- **Python 3.12+**: Required for the backend
- **uv**: Recommended for dependency management

## Docker Compose (Full Stack)

For a complete containerized setup with PostgreSQL, Redis, Celery workers, and Studio, use the bundled Docker Compose configuration.

### Compose Modes

Orcheo ships with two compose entrypoints plus a staging overlay:

- Root `docker-compose.yml` is for live local development. It bind-mounts source code, runs the backend with `--reload`, and serves Studio via the Vite dev server.
- `deploy/stack/docker-compose.yml` is the production-style stack contract. It runs built images with no source bind mounts or hot reload.
- `deploy/stack/docker-compose.staging.yml` layers on top of the production stack file to build those same services from the current repo checkout on the staging host.

### Quick Start

1. **Set up the stack** using the CLI (this downloads compose files and creates `.env` automatically):
   ```bash
   orcheo install --start-stack
   ```

   The CLI syncs stack assets to `~/.orcheo/stack` (override with `ORCHEO_STACK_DIR`).
   On fresh installs, secure values are auto-generated for `ORCHEO_POSTGRES_PASSWORD`,
   `ORCHEO_VAULT_ENCRYPTION_KEY`, and `ORCHEO_CHATKIT_TOKEN_SIGNING_KEY`.

2. **(Optional) Configure secrets** in `~/.orcheo/stack/.env`:

    You only need to edit `.env` if you want custom values or need to configure
    `VITE_ORCHEO_CHATKIT_DOMAIN_KEY` for ChatKit. The setup flow prompts for this
    key and allows skipping; if skipped, ChatKit UI features remain disabled
    until you set it (or rerun `orcheo install --chatkit-domain-key <key>`). Be aware that changing
    `ORCHEO_VAULT_ENCRYPTION_KEY` after storing credentials will make them unreadable.

3. **Verify services are running**:
    - Backend API: http://localhost:2025
    - Studio UI: http://localhost:2026 (may take 2-3 minutes on first startup while npm installs dependencies)

### Public Self-Hosted Ingress (Bundled Caddy)

Use this path on a reachable self-hosted Linux host, such as a cloud VM or an on-prem server with inbound routing already in place.

1. **Confirm prerequisites before install**:
   - DNS for your chosen hostname already points at the host running Docker.
   - Inbound `80` and `443` reach that host.
   - You are not relying on localhost-only networking or a NAT-restricted workstation.
2. **Install with bundled ingress enabled**:
   ```bash
   orcheo install --public-ingress --public-host orcheo.example.com --start-stack
   ```
3. **Verify the public origin**:
   - Public UI: `https://orcheo.example.com/`
   - Public API: `https://orcheo.example.com/api/system/info`
   - Public workflow WebSocket base: `wss://orcheo.example.com/ws/workflow/<workflow_id>`

`orcheo install` writes the public-origin contract into `~/.orcheo/stack/.env`:
- `ORCHEO_API_URL=https://<host>`
- `VITE_ORCHEO_BACKEND_URL=https://<host>`
- `ORCHEO_CHATKIT_PUBLIC_BASE_URL=https://<host>`
- `ORCHEO_CORS_ALLOW_ORIGINS=https://<host>` plus localhost origins for development
- `VITE_ORCHEO_ALLOWED_HOSTS=localhost,127.0.0.1,<host>`

Bundled Caddy is the recommended ingress for reachable self-hosted installs. It is not a replacement for Cloudflare Tunnel when inbound ports are unavailable.

### Managing Services

```bash
STACK_DIR="${ORCHEO_STACK_DIR:-$HOME/.orcheo/stack}"

# View logs
docker compose -f "$STACK_DIR/docker-compose.yml" --project-directory "$STACK_DIR" logs -f backend
docker compose -f "$STACK_DIR/docker-compose.yml" --project-directory "$STACK_DIR" logs -f worker
docker compose -f "$STACK_DIR/docker-compose.yml" --project-directory "$STACK_DIR" logs -f celery-beat
docker compose -f "$STACK_DIR/docker-compose.yml" --project-directory "$STACK_DIR" logs -f studio
docker compose -f "$STACK_DIR/docker-compose.yml" --project-directory "$STACK_DIR" logs -f caddy

# Stop all services
docker compose -f "$STACK_DIR/docker-compose.yml" --project-directory "$STACK_DIR" down

# Refresh to the latest published stack image
docker compose -f "$STACK_DIR/docker-compose.yml" --project-directory "$STACK_DIR" pull
docker compose -f "$STACK_DIR/docker-compose.yml" --project-directory "$STACK_DIR" up -d
```

### Staging From A Repo Checkout

Use this flow when a staging machine should run the production-style stack but build images directly from unreleased source code in the current git checkout.

```bash
git pull
# make staging-* will reuse ~/.orcheo/stack/.env when present,
# or create it from deploy/stack/.env.example on first run
make staging-build
make staging-up
```

These targets use `deploy/stack/docker-compose.yml` together with `deploy/stack/docker-compose.staging.yml`. The resulting stack:

- keeps the production service topology
- builds backend, worker, beat, and Studio from local source
- avoids source bind mounts and hot reload

Useful commands:

```bash
make staging-config
make staging-logs
make staging-down
```

### Local Testing Without OAuth

For local development without OAuth, set `ORCHEO_AUTH_MODE=optional` in your `.env` file. This allows unauthenticated access to the API.

### Tunnel-Based Development

Keep Cloudflare Tunnel or a similar tunnel product for cases where the Orcheo host is not directly reachable from the internet, especially localhost development, callback testing from laptops, and NAT-restricted environments. Bundled Caddy expects direct inbound `80/443` access instead.

When using Cloudflare Tunnel with separate browser and API hostnames, keep bundled public ingress disabled and point tunnel routes at the direct localhost ports:

```env
ORCHEO_PUBLIC_INGRESS_ENABLED=false
ORCHEO_API_URL=https://orcheo.example.com
VITE_ORCHEO_BACKEND_URL=https://orcheo.example.com
ORCHEO_CORS_ALLOW_ORIGINS=https://orcheo-studio.example.com
ORCHEO_CHATKIT_PUBLIC_BASE_URL=https://orcheo-studio.example.com
VITE_ORCHEO_ALLOWED_HOSTS=localhost,127.0.0.1,orcheo-studio.example.com
```

Use one hostname for backend requests and websocket traffic, and the Studio hostname as the browser origin:
- `https://orcheo.example.com` -> backend API and `/ws/*`
- `https://orcheo-studio.example.com` -> Studio UI

If you previously ran `orcheo install` after the Caddy ingress rollout and your tunnel deployment started returning `OPTIONS ... 400`, check these values first. The common failure mode is that tunnel-specific origins were reset to localhost defaults in `~/.orcheo/stack/.env`.

## Installation (Manual)

The project ships with everything needed to spin up the FastAPI runtime on SQLite for local development.

### From Source (Development)

```bash
uv sync --all-groups
```

### From PyPI

```bash
uv add orcheo orcheo-backend orcheo-sdk
```

### Activating the Virtual Environment

After installation, activate the virtual environment (optional but recommended):

```bash
source .venv/bin/activate  # On macOS/Linux
# or
.venv\Scripts\activate     # On Windows
```

Once activated, you can run commands without the `uv run` prefix.

## Running the API Server

Start the development server:

```bash
orcheo-dev-server
```

### Verifying the Setup

```bash
orcheo-test
```

### Authentication

Orcheo supports flexible authentication modes configured via `ORCHEO_AUTH_MODE`:

- **disabled**: No authentication (development only)
- **optional**: Validates tokens when provided but not required
- **required**: All requests must include valid credentials (recommended for production)

For detailed authentication setup including bootstrap tokens, service tokens, and OAuth configuration, see the [Authentication Guide](authentication_guide.md).

## Next Steps

- **[CLI Reference](cli_reference.md)** — Command reference for the `orcheo` CLI
- **[Studio](studio.md)** — Workflow monitor, config editor, and Credential Vault manager
- **[MCP Integration](mcp_integration.md)** — Connect AI assistants to Orcheo
- **[Authentication Guide](authentication_guide.md)** — Detailed authentication configuration
- **[Developer Guide](developer_guide.md)** — Contributing to Orcheo

## Upgrade Recovery Notes

If setup/upgrade is interrupted:

1. Re-run `orcheo install` (idempotent reconciliation is the default path).
2. If stack services are inconsistent, run:
   `STACK_DIR="${ORCHEO_STACK_DIR:-$HOME/.orcheo/stack}"` then
   `docker compose -f "$STACK_DIR/docker-compose.yml" --project-directory "$STACK_DIR" down`
   and
   `docker compose -f "$STACK_DIR/docker-compose.yml" --project-directory "$STACK_DIR" up -d`.
3. If local files were customized and you want a clean baseline, delete
   `~/.orcheo/stack` and re-run `orcheo install`.
