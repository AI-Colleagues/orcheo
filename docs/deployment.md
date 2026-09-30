# Deployment Recipes

This guide captures reference deployment flows for running Orcheo locally during development and hosting the service for teams. Each recipe lists the required environment variables, supporting services, and common verification steps.

## Local Development (PostgreSQL)

This setup mirrors the default configuration that the tests exercise. It is ideal when you want to iterate on nodes, run the FastAPI server, and execute LangGraph workflows from the command line.

1. **Install dependencies**
   ```bash
   uv sync --all-groups
   ```
2. **Configure environment variables**
   ```bash
   cp .env.example .env
   ```
   - Multi-workspace is always on. Users must already belong to a workspace or create one through the self-service API after login.
   - Keep `ORCHEO_WORKSPACE_BACKEND=postgres` and `ORCHEO_POSTGRES_DSN` pointed at a durable database so memberships and workspace metadata survive backend restarts.
3. **Start the API server**
   ```bash
   make dev-server
   ```
4. **Run an example workflow**
   - Send a websocket message to `ws://localhost:2025/ws/workflow/<workflow_id>` (see the [Authentication Guide](authentication_guide.md#websocket-authentication) for token options), or trigger a run with `orcheo workflow run <workflow_id>`.

**Verification**: Run `uv run pytest` to validate the environment. The test suite uses the same backend factories as the server.

_Vault note_: Set `ORCHEO_VAULT_BACKEND=postgres` and `ORCHEO_VAULT_ENCRYPTION_KEY` before starting the backend so credential encryption is configured from the first run.

_Repository note_: Local development uses the PostgreSQL workflow repository. Set `ORCHEO_REPOSITORY_BACKEND=postgres` and `ORCHEO_POSTGRES_DSN` so runs, triggers, and workflow state persist durably.

### Workspace Bring-up

Workspace scoping is always on. Before exposing the API:

1. **Postgres workspace store**
   - Set `ORCHEO_WORKSPACE_BACKEND=postgres` and provide `ORCHEO_POSTGRES_DSN`.
2. **Memberships**
   - Confirm every user has at least one workspace membership. Service tokens
     and dev logins must carry `workspace_ids` in their claims.
3. **Verification**
   - Hit `/api/workspaces/me` and confirm the Studio workspace badge to ensure
     the resolved workspace matches expectations.

## Docker Compose (PostgreSQL, multi-container)

Use this recipe when you want an isolated environment that mimics production with a dedicated PostgreSQL database.

1. **Create `docker-compose.yml`**
   ```yaml
   services:
     orcheo:
       build: .
       command: uvicorn orcheo_backend.app:app --host 0.0.0.0 --port 2025
       environment:
         ORCHEO_HOST: 0.0.0.0
         ORCHEO_PORT: "2025"
         ORCHEO_CHECKPOINT_BACKEND: postgres
         ORCHEO_GRAPH_STORE_BACKEND: postgres
         ORCHEO_REPOSITORY_BACKEND: postgres
         ORCHEO_WORKSPACE_BACKEND: postgres
         ORCHEO_CHATKIT_BACKEND: postgres
         ORCHEO_VAULT_BACKEND: postgres
         ORCHEO_VAULT_ENCRYPTION_KEY: change-me
         ORCHEO_POSTGRES_DSN: postgresql://orcheo:orcheo@postgres:5432/orcheo
       ports:
         - "2025:2025"
       depends_on:
         - postgres
     postgres:
       image: postgres:16
       environment:
         POSTGRES_USER: orcheo
         POSTGRES_PASSWORD: orcheo
         POSTGRES_DB: orcheo
       ports:
         - "5432:5432"
       volumes:
         - postgres-data:/var/lib/postgresql/data
   volumes:
     postgres-data:
   ```
2. **Build and start**
   ```bash
   docker compose up --build
   ```
3. **Connect**
   Access the API via `http://localhost:2025`. The Postgres database is stored inside the named volume so runs persist across container restarts.

**Verification**: `curl http://localhost:2025/api/system/info` confirms the container is healthy.

_Vault note_: Rotate `ORCHEO_VAULT_ENCRYPTION_KEY` regularly and back up the Postgres volume alongside the database.

## Lean Single Image (Backend + Studio)

`ghcr.io/ai-colleagues/orcheo-lean` packages the backend and Studio, both built
from source at the tagged revision, into one image. The backend serves Studio
on the same origin (port 2025), so there is no separate Studio container.

### With Celery worker, Celery Beat, and Redis

`deploy/lean/docker-compose.yml` runs the lean image three times (backend,
worker, and Beat) next to Redis, with in-process execution and cron turned off on the
backend. PostgreSQL is not bundled: the stack uses a Supabase database through
`ORCHEO_POSTGRES_DSN`, which must be set. Use the Supabase transaction pooler
connection string (Supavisor, `*.pooler.supabase.com` port 6543, from the
project's Connect dialog). It works over IPv4 and lets the backend, worker, and
Beat share a few server connections. The session pooler (port 5432) holds one
server connection per client connection, so it fails with `EMAXCONNSESSION`
once the project's pool size (15 on small projects) is used up; if you stay on
it, lower `ORCHEO_POSTGRES_POOL_MAX_SIZE`. Orcheo disables server-side prepared
statements, which transaction pooling does not support.

The direct connection and the dedicated pooler (`db.<ref>.supabase.co`) are
IPv6-only. The compose file enables IPv6 on its network
(`ORCHEO_LEAN_ENABLE_IPV6`, default `true`), which needs Docker Engine 27 or
newer and a host with outbound IPv6. On Docker Desktop, pick dual IPv4/IPv6
networking under Settings > Resources > Network. Set
`ORCHEO_LEAN_ENABLE_IPV6=false` on older engines, where creating an IPv6
network without a configured subnet fails.

From the repository root, create `deploy/lean/.env` from the template and fill
it in, then start the stack:

```bash
cp deploy/lean/.env.example deploy/lean/.env
docker compose -f deploy/lean/docker-compose.yml up -d --build
```

Compose reads `deploy/lean/.env`, not the repository-root `.env`. `--build`
builds `Dockerfile.lean` from your checkout; the image includes the ChatKit
widgets from `deploy/stack/chatkit_widgets`, so nothing is mounted. To run a published
image, set `ORCHEO_LEAN_IMAGE=ghcr.io/ai-colleagues/orcheo-lean:<version>` and
use `--no-build`. `ORCHEO_POSTGRES_DSN` and `ORCHEO_VAULT_ENCRYPTION_KEY` must
be set (the optional `.env` is read for them). Set `ORCHEO_LEAN_PUBLIC_URL` to the browser-facing origin used
for invitation links, the MCP sign-in consent page and CORS (default
`http://localhost:2025`), and
`ORCHEO_LEAN_PORT` to change the host port.

Without a checkout, `orcheo install --lean` downloads `docker-compose.yml` and
`.env.example` from `deploy/lean/` at the newest `lean-v*` release into
`~/.orcheo/lean`, prompts for the Supabase connection string and the email
domains allowed to sign in, writes `.env` with generated secrets and the pinned
`ORCHEO_LEAN_IMAGE`, and starts the stack with the published image. The
template keeps local defaults (auth disabled, CLI uploads allowed, port bound to
127.0.0.1). An `https://` backend URL at the prompt switches sign-in to
required; otherwise set `ORCHEO_AUTH_MODE=required` before exposing it.
`ORCHEO_AUTH_ALLOWED_EMAIL_DOMAINS` limits sign-in to the listed domains once
sign-in is required.

The lean services use `restart: unless-stopped`. The backend's Docker health
check calls `/api/system/ready`, which tests Redis from the backend container;
worker and Beat health checks also connect to Redis. These checks verify broker
reachability, not that the worker is executing or Beat is scheduling. The
installer waits for all services to become healthy. Redis availability is an
intentional part of the lean backend container's health status; use
`/api/system/health` as the backend liveness probe and `/api/system/ready` as
the readiness probe in an orchestrator that restarts unhealthy containers.
Docker Compose does not automatically restart a container merely because its
health check becomes unhealthy, so monitor Compose health and alert on an
unhealthy service or a stopped worker or Beat.

After a Redis or Docker network incident, recreate the lean containers if the
`redis` service name does not resolve from the backend:

```bash
cd ~/.orcheo/lean
docker compose up -d --no-build --force-recreate --wait --wait-timeout 120
docker compose exec backend python -m orcheo_backend.app.broker_healthcheck
docker compose ps
```

Recreation retains the named `redis_data` and `orcheo_data` volumes. Do not use
`down -v` during recovery. Record `docker compose version` and
`docker network inspect orcheo-lean_default` if a service alias disappears.
Use Docker's maintained Compose plugin rather than an outdated distribution
package when diagnosing a repeat network issue.

Trigger-created runs carry a persisted dispatch flag. While the backend is up,
it checks PostgreSQL once per minute and republishes up to 20 flagged runs that
have remained pending for at least two minutes **without a confirmed enqueue**.
Each run is claimed across backend processes and retried no more than once
every five minutes after a failed attempt. Runs already accepted by Redis are
not republished simply because the worker queue is busy. Worker start
transitions lock the PostgreSQL row so duplicate queue messages cannot start
the same run twice. Monitor the count and age of pending runs where
`dispatch_requested = TRUE`; sustained growth means execution is stalled.

```sql
SELECT COUNT(*) AS pending_dispatches, MIN(created_at) AS oldest_created_at
  FROM workflow_runs
 WHERE status = 'pending' AND dispatch_requested = TRUE;
```

If Redis loses a message after acknowledging a publish, the run will still be
marked as enqueued. Automatic recovery of broker data loss after acceptance is
outside the reconciler's scope. The lean Compose service enables Redis AOF
persistence and keeps it in the `redis_data` volume; preserve and back up that
volume. If the broker data is lost, verify that the run is absent from the
worker queue before setting `enqueue_confirmed = FALSE` for that run to request
replay. This avoids creating duplicate queue messages during a normal backlog.

The concurrency quota also counts pending runs deliberately created through
the API and runs marked `running`. A crashed worker can leave a run in
`running`, which needs operator review: age alone cannot prove that execution
has stopped. Inspect the worker and run history before marking an orphaned run
failed through `POST /api/runs/{run_id}/fail`. Mark or cancel abandoned pending
API runs through the run API as well. Automatic recovery requires execution
ownership and liveness tracking ([issue #449](https://github.com/AI-Colleagues/orcheo/issues/449)).
To find candidates:

```sql
SELECT id, workspace_id, status, created_at, updated_at
  FROM workflow_runs
 WHERE status IN ('pending', 'running')
 ORDER BY updated_at;
```

Every five minutes the backend logs a warning for each workspace and status
with pending or running runs that have not changed for at least one hour. The
warning includes the count and oldest update time. Alert on
`Stale active workflow runs` in backend logs, then inspect the worker and run
history before changing run status. Long-running workflows can also trigger
this warning; it does not automatically release quota slots.

Runs created before this dispatch flag was added need operator review before
replay because some API-created pending runs are deliberately idle. After
checking which run IDs were meant to execute and that they have not already
started, mark only those IDs for reconciliation in PostgreSQL:

```sql
UPDATE workflow_runs
   SET dispatch_requested = TRUE
 WHERE id IN ('reviewed-run-id-1', 'reviewed-run-id-2')
   AND status = 'pending';
```

Cron state retains its last dispatched occurrence. After an outage, the cron
dispatcher creates at most one due occurrence per workflow per pass; schedules
with overlap protection wait for that run to finish before another is created.
If a schedule has never dispatched and has no `start_at`, it uses the current
time as its baseline, so occurrences from before recovery are not created.
Review the outage window for missed occurrences and the resulting backlog.

### Single container

Without a worker or Beat, the backend runs executions and cron triggers
in-process, so PostgreSQL is the only other service:

```bash
docker run -d --name orcheo -p 2025:2025 \
  -v orcheo_data:/data \
  -e ORCHEO_POSTGRES_DSN=postgresql://orcheo:orcheo@db.example:5432/orcheo \
  -e ORCHEO_VAULT_ENCRYPTION_KEY="$(openssl rand -hex 32)" \
  -e ORCHEO_AUTH_MODE=required \
  -e ORCHEO_AUTH_JWT_SECRET="$(openssl rand -hex 32)" \
  -e ORCHEO_STUDIO_URL=https://orcheo.example.com \
  ghcr.io/ai-colleagues/orcheo-lean:latest
```

Studio's `VITE_ORCHEO_*` settings are read from the container environment at
startup, as with the stack Studio image. `VITE_ORCHEO_BACKEND_URL` can stay
unset because Studio calls the backend on its own origin, and
`VITE_ORCHEO_APPS_BASE_DOMAIN` defaults to `ORCHEO_APPS_BASE_DOMAIN`. Hosted
apps still need the separate app gateway, so leave `ORCHEO_HOSTED_APPS_ENABLED`
off with this image unless you run one. In single-container mode, run exactly
one container per database: the in-process cron loop is only safe in a single
backend process, so use the Compose setup above when you need more.

## Reachable Self-Hosted Host (Bundled Caddy)

This is the standard public self-hosted recipe for Orcheo on a reachable Linux host. The bundled stack keeps backend, Studio, Postgres, Redis, worker, and beat on the Docker network while Caddy is the only service that needs public `80/443`.

1. **Prepare the host**
   - Point your DNS hostname at the machine that will run Docker.
   - Open inbound `80` and `443`.
   - Install Docker and the Orcheo SDK.
2. **Install the stack with public ingress**
   ```bash
   orcheo install --public-ingress --public-host orcheo.example.com --start-stack
   ```
3. **Understand the routing contract**
   - `https://orcheo.example.com/` -> Studio
   - `https://orcheo.example.com/api/...` -> backend HTTP routes
   - `wss://orcheo.example.com/ws/...` -> backend WebSocket routes
4. **Inspect the generated stack config when needed**
   - `COMPOSE_PROFILES=public-ingress` enables Caddy TLS ingress. Backend and Studio remain accessible on their direct localhost ports (`2025` and `2026` by default).
   - `ORCHEO_CADDY_BACKEND_UPSTREAMS` controls the backend upstream pool for `/api/*` and `/ws/*`.
5. **Verify the public origin**
   ```bash
   curl -I https://orcheo.example.com/
   curl https://orcheo.example.com/api/system/info
   ```

### Replica Topology

The initial supported load-balancing topology is one logical deployment with multiple backend replicas that all share the same Postgres and Redis services. Caddy load-balances only replicas of that same deployment.

Set explicit backend upstreams in `~/.orcheo/stack/.env` when you add more backend replicas:

```env
ORCHEO_CADDY_BACKEND_UPSTREAMS=backend:2025 backend-2:2025 backend-3:2025
```

Use this pattern only when the replicas share the same repository, checkpoint, ChatKit, and vault state through shared Postgres and Redis. Do not use one hostname and one path to multiplex isolated customer-specific stacks.

### When To Put Something In Front Of Caddy

Bundled Caddy is appropriate for standard self-hosted installs and moderate scale. Prefer a cloud-managed load balancer, ingress controller, CDN, or WAF in front of Caddy, or instead of Caddy, when you need:

- higher-volume internet edge traffic
- managed certificates outside the host
- WAF, bot management, or DDoS shielding
- platform-native ingress on Kubernetes or managed container platforms

## Published Prerelease Staging Host

Use the published prerelease channel when a staging host should validate the same
artifacts that prerelease users will install:

```bash
orcheo install --staging --start-stack
```

The installer resolves the newest `stack-vX.Y.Z-{alpha,beta,rc}.N` tag, syncs
the stack assets from that exact tag, and pins both the stack and Studio images
to the resolved version. Use `--stack-version` instead when the host must remain
on one exact prerelease.

For unreleased source development, use the root `docker-compose.yml`.

## Cloudflare Tunnel Or Similar Split-Origin Tunnel

Use this recipe when the host is not directly reachable or when you intentionally keep Studio and backend on separate public hostnames behind a tunnel. In this topology, bundled Caddy stays off and the tunnel forwards to the direct localhost ports published by backend and Studio.

1. **Install the stack without bundled public ingress**
   ```bash
   orcheo install --start-stack
   ```
2. **Point your tunnel routes at the direct localhost ports**
   - `https://orcheo.example.com` -> `http://localhost:2025`
   - `https://orcheo-studio.example.com` -> `http://localhost:2026`
3. **Set the generated stack env to the split-origin contract**
   ```env
   ORCHEO_PUBLIC_INGRESS_ENABLED=false
   ORCHEO_API_URL=https://orcheo.example.com
   VITE_ORCHEO_BACKEND_URL=https://orcheo.example.com
   ORCHEO_CORS_ALLOW_ORIGINS=https://orcheo-studio.example.com
   ORCHEO_CHATKIT_PUBLIC_BASE_URL=https://orcheo-studio.example.com
   VITE_ORCHEO_ALLOWED_HOSTS=localhost,127.0.0.1,orcheo-studio.example.com
   ```
4. **Restart the stack after editing `~/.orcheo/stack/.env`**
   ```bash
   orcheo stack --stop
   orcheo stack --start
   ```
5. **Verify the public origins**
   ```bash
   curl -I https://orcheo-studio.example.com/
   curl https://orcheo.example.com/api/system/info
   ```

The important distinction is that backend-facing values use the backend hostname, while browser-origin values use the Studio hostname. If these are collapsed back to `localhost` values, browsers will fail preflight requests and the backend will log `OPTIONS ... 400`.

## Managed Hosting (PostgreSQL, async pool)

This deployment targets platforms such as Fly.io, Railway, or Kubernetes where Postgres is available as a managed service.

1. **Provision PostgreSQL**
   - Create a database and note the DSN, e.g. `postgresql://user:pass@host:5432/orcheo`.
   - Ensure the `psycopg[binary,pool]` and `langgraph[postgres]` extras are installed (already defined in `pyproject.toml`).
2. **Configure environment variables**
   ```bash
   export ORCHEO_CHECKPOINT_BACKEND=postgres
   export ORCHEO_POSTGRES_DSN=postgresql://user:pass@host:5432/orcheo
   export ORCHEO_REPOSITORY_BACKEND=postgres
   export ORCHEO_CHATKIT_BACKEND=postgres
   export ORCHEO_HOST=0.0.0.0
   export ORCHEO_PORT=2025
   export ORCHEO_VAULT_BACKEND=postgres
   export ORCHEO_VAULT_ENCRYPTION_KEY=change-me
   export ORCHEO_VAULT_TOKEN_TTL_SECONDS=900
   ```
3. **Deploy the application**
   - **Docker image**: Build with `docker build -t orcheo-app .` and push to your registry.
   - **Fly.io example**:
     ```bash
     fly launch --no-deploy
     fly secrets set ORCHEO_POSTGRES_DSN=...
     fly deploy
     ```
  - Ensure the container command starts uvicorn: `uvicorn orcheo_backend.app:app --host 0.0.0.0 --port ${PORT}`.
4. **Health checks**
   - Expose `/docs` and `/openapi.json` for HTTP checks.
   - Use `/ws/workflow/{workflow_id}` for synthetic workflow runs during smoke tests.

**Verification**: Run `uv run pytest tests/test_persistence.py` locally with the `ORCHEO_CHECKPOINT_BACKEND=postgres` environment variable set and a reachable Postgres DSN to mirror production behavior.

_Vault note_: Managed environments should prefer KMS-integrated vaults. Configure IAM policies so only the Orcheo runtime can decrypt with the specified key.

## Operational Tips

- **Secrets**: Prefer platform-specific secret managers (Fly Secrets, Railway variables, AWS Parameter Store) and never bake DSNs or vault encryption keys into images.
- **Observability**: Route application logs to structured logging (e.g., stdout + centralized collector) and enable OpenTelemetry tracing via the `ORCHEO_TRACING_*` variables (see [OpenTelemetry Tracing](otel_tracing/README.md)).
- **Scaling**: The FastAPI app is stateless. Scale horizontally by adding replicas while pointing them at the same checkpoint database. With bundled Caddy, keep replica pools limited to one logical deployment that shares Postgres and Redis.
- **Backups**: Schedule database backups (pg_dump or managed snapshots) to protect workflow history and run states.

Use Cloudflare Tunnel when the host is not directly reachable from the internet, or when you intentionally want tunnel-managed public hostnames in front of the direct localhost ports. For reachable hosts with direct inbound ports and one shared origin, bundled Caddy is the simpler default.

These recipes will evolve as additional milestones introduce credential vaulting, trigger services, and observability pipelines.
