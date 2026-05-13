# Deployment Recipes

This guide captures reference deployment flows for running Orcheo locally during development and hosting the service for teams. Each recipe lists the required environment variables, supporting services, and common verification steps.

## Local Development (SQLite, single process)

This setup mirrors the default configuration that the tests exercise. It is ideal when you want to iterate on nodes, run the FastAPI server, and execute LangGraph workflows from the command line.

1. **Install dependencies**
   ```bash
   uv sync --all-groups
   ```
2. **Configure environment variables**
   ```bash
   cp .env.example .env
   ```
   - For multi-workspace installs, set `ORCHEO_MULTI_WORKSPACE_ENABLED=true` once the backfill and repo-retrofit release has been verified.
   - There is no bootstrapped default workspace anymore; users must already belong to a workspace or create one through the self-service API after login.
   - Keep `ORCHEO_WORKSPACE_BACKEND=sqlite` and `ORCHEO_WORKSPACE_SQLITE_PATH` pointed at shared durable storage so memberships and workspace metadata survive backend restarts.
3. **Start the API server**
   ```bash
   make dev-server
   ```
4. **Run an example workflow**
   - Send a websocket message to `ws://localhost:8000/ws/workflow/<workflow_id>` with a payload matching the schema in `tests/test_main.py`.

**Verification**: Run `uv run pytest` to validate the environment. The test suite opens an SQLite connection through the same helper used by the server.

_Vault note_: The default `.env.example` now stores credentials in an encrypted SQLite vault at `.orcheo/vault.sqlite`. The backend generates and caches the AES key alongside the database on first start. Switch `ORCHEO_VAULT_BACKEND` to `inmemory` for ephemeral secrets or set `ORCHEO_VAULT_ENCRYPTION_KEY` to supply a managed key.

_Repository note_: Local development now defaults to a SQLite-backed workflow repository stored at `~/.orcheo/workflows.sqlite`. Override `ORCHEO_REPOSITORY_BACKEND` to `inmemory` if you prefer ephemeral state or set `ORCHEO_REPOSITORY_SQLITE_PATH` to relocate the database file. The in-memory backend does not enqueue webhook/cron/manual triggers for execution, so runs remain `PENDING` unless you drive execution manually.

### Workspace Rollout

Use this sequence when enabling workspace scoping on an existing installation.

1. **Flag off first**
   - Deploy the code with `ORCHEO_MULTI_WORKSPACE_ENABLED=false`.
   - Set `ORCHEO_WORKSPACE_BACKEND=postgres` and provide `ORCHEO_POSTGRES_DSN` before creating or migrating memberships.
2. **Verify the backfill release**
   - Confirm existing users have at least one membership before turning the feature on.
   - Confirm existing workflows, runs, credentials, and graph records resolve under the intended workspaces.
   - Check `/api/workspaces/me` and the Canvas workspace badge to ensure the resolved workspace matches expectations.
3. **Turn workspace scoping on**
   - Flip `ORCHEO_MULTI_WORKSPACE_ENABLED=true` in the backend, worker, beat, and stack env files together.
   - Keep `ORCHEO_WORKSPACE_BACKEND=postgres` in those same env files and restart the stack so the API, Celery worker, and scheduled jobs pick up the same workspace settings.
4. **Rollback**
   - If any cross-workspace regression appears, switch the flag back to `false`, restart the stack, and keep the data in place for inspection.
   - Because memberships are explicit now, rollback only restores the old routing behavior; it does not recreate or depend on a shared default workspace.

## Docker Compose (SQLite, multi-container)

Use this recipe when you want an isolated environment that mimics production without provisioning a database. It pairs the FastAPI app with a volume-mounted SQLite database.

1. **Create `docker-compose.yml`**
   ```yaml
   services:
     orcheo:
       build: .
       command: uvicorn orcheo_backend.app:app --host 0.0.0.0 --port 8000
       environment:
         ORCHEO_HOST: 0.0.0.0
         ORCHEO_PORT: "8000"
         ORCHEO_CHECKPOINT_BACKEND: sqlite
         ORCHEO_SQLITE_PATH: /data/orcheo.sqlite3
         ORCHEO_REPOSITORY_BACKEND: sqlite
         ORCHEO_REPOSITORY_SQLITE_PATH: /data/workflows.sqlite3
         ORCHEO_VAULT_BACKEND: file
         ORCHEO_VAULT_ENCRYPTION_KEY: change-me
         ORCHEO_VAULT_LOCAL_PATH: /data/vault.sqlite
         ORCHEO_VAULT_TOKEN_TTL_SECONDS: "3600"
       ports:
         - "8000:8000"
       volumes:
         - orcheo-data:/data
   volumes:
     orcheo-data:
   ```
2. **Build and start**
   ```bash
   docker compose up --build
   ```
3. **Connect**
   Access the API via `http://localhost:8000`. The checkpoint database is stored inside the named volume so runs persist across container restarts.

**Verification**: `docker compose exec orcheo uv run pytest tests/test_main.py` confirms the container is healthy.

_Vault note_: The compose example writes encrypted secrets to `/data/vault.sqlite`. Rotate `ORCHEO_VAULT_ENCRYPTION_KEY` regularly and back up the volume alongside the checkpoint database.

## Reachable Self-Hosted Host (Bundled Caddy)

This is the standard public self-hosted recipe for Orcheo on a reachable Linux host. The bundled stack keeps backend, Canvas, Postgres, Redis, worker, and beat on the Docker network while Caddy is the only service that needs public `80/443`.

1. **Prepare the host**
   - Point your DNS hostname at the machine that will run Docker.
   - Open inbound `80` and `443`.
   - Install Docker and the Orcheo SDK.
2. **Install the stack with public ingress**
   ```bash
   orcheo install --public-ingress --public-host orcheo.example.com --start-stack
   ```
3. **Understand the routing contract**
   - `https://orcheo.example.com/` -> Canvas
   - `https://orcheo.example.com/api/...` -> backend HTTP routes
   - `wss://orcheo.example.com/ws/...` -> backend WebSocket routes
4. **Inspect the generated stack config when needed**
   - `COMPOSE_PROFILES=public-ingress` enables Caddy TLS ingress. Backend and Canvas remain accessible on their direct localhost ports (`8000` and `5173` by default).
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
ORCHEO_CADDY_BACKEND_UPSTREAMS=backend:8000 backend-2:8000 backend-3:8000
```

Use this pattern only when the replicas share the same repository, checkpoint, ChatKit, and vault state through shared Postgres and Redis. Do not use one hostname and one path to multiplex isolated customer-specific stacks.

### When To Put Something In Front Of Caddy

Bundled Caddy is appropriate for standard self-hosted installs and moderate scale. Prefer a cloud-managed load balancer, ingress controller, CDN, or WAF in front of Caddy, or instead of Caddy, when you need:

- higher-volume internet edge traffic
- managed certificates outside the host
- WAF, bot management, or DDoS shielding
- platform-native ingress on Kubernetes or managed container platforms

## Source-Built Staging Host

Use this recipe when the staging machine deploys from a full git checkout and should stay close to the production stack without waiting for package or image releases.

1. **Pull the latest code on the staging host**
   ```bash
   git pull
   ```
2. **Configure stack environment values**
   - `make staging-*` reuses `~/.orcheo/stack/.env` when it already exists.
   - On first run it creates `~/.orcheo/stack/.env` from `deploy/stack/.env.example`, then preserves later edits while backfilling newly introduced keys.
3. **Build the staging images from source**
   ```bash
   make staging-build
   ```
4. **Start the production-style staging stack**
   ```bash
   make staging-up
   ```
5. **Inspect or stop the stack when needed**
   ```bash
   make staging-config
   make staging-logs
   make staging-down
   ```

The staging targets combine `deploy/stack/docker-compose.yml` with `deploy/stack/docker-compose.staging.yml`. This keeps the same runtime topology as production while swapping published images for local source builds:

- backend, worker, and beat are built from the monorepo checkout
- Canvas is built from local `apps/canvas` source and served by nginx
- there are no source bind mounts, Vite dev server processes, or backend `--reload` flags

## Cloudflare Tunnel Or Similar Split-Origin Tunnel

Use this recipe when the host is not directly reachable or when you intentionally keep Canvas and backend on separate public hostnames behind a tunnel. In this topology, bundled Caddy stays off and the tunnel forwards to the direct localhost ports published by backend and Canvas.

1. **Install the stack without bundled public ingress**
   ```bash
   orcheo install --start-stack
   ```
2. **Point your tunnel routes at the direct localhost ports**
   - `https://orcheo.example.com` -> `http://localhost:8000`
   - `https://orcheo-canvas.example.com` -> `http://localhost:5173`
3. **Set the generated stack env to the split-origin contract**
   ```env
   ORCHEO_PUBLIC_INGRESS_ENABLED=false
   ORCHEO_API_URL=https://orcheo.example.com
   VITE_ORCHEO_BACKEND_URL=https://orcheo.example.com
   ORCHEO_CORS_ALLOW_ORIGINS=https://orcheo-canvas.example.com
   ORCHEO_CHATKIT_PUBLIC_BASE_URL=https://orcheo-canvas.example.com
   VITE_ALLOWED_HOSTS=localhost,127.0.0.1,orcheo-canvas.example.com
   ```
4. **Restart the stack after editing `~/.orcheo/stack/.env`**
   ```bash
   orcheo stack --stop
   orcheo stack --start
   ```
5. **Verify the public origins**
   ```bash
   curl -I https://orcheo-canvas.example.com/
   curl https://orcheo.example.com/api/system/info
   ```

The important distinction is that backend-facing values use the backend hostname, while browser-origin values use the Canvas hostname. If these are collapsed back to `localhost` values, browsers will fail preflight requests and the backend will log `OPTIONS ... 400`.

## Managed Hosting (PostgreSQL, async pool)

This deployment targets platforms such as Fly.io, Railway, or Kubernetes where Postgres is available as a managed service.

1. **Provision PostgreSQL**
   - Create a database and note the DSN, e.g. `postgresql://user:pass@host:5432/orcheo`.
   - Ensure the `psycopg[binary,pool]` and `langgraph[postgres]` extras are installed (already defined in `pyproject.toml`).
2. **Configure environment variables**
   ```bash
   export ORCHEO_CHECKPOINT_BACKEND=postgres
   export ORCHEO_POSTGRES_DSN=postgresql://user:pass@host:5432/orcheo
   export ORCHEO_REPOSITORY_BACKEND=inmemory
   export ORCHEO_CHATKIT_BACKEND=postgres
   export ORCHEO_HOST=0.0.0.0
   export ORCHEO_PORT=8000
   export ORCHEO_VAULT_BACKEND=aws_kms
   export ORCHEO_VAULT_ENCRYPTION_KEY=alias/orcheo-runtime
   export ORCHEO_VAULT_AWS_REGION=us-west-2
   export ORCHEO_VAULT_AWS_KMS_KEY_ID=1234abcd-12ab-34cd-56ef-1234567890ab
   export ORCHEO_VAULT_TOKEN_TTL_SECONDS=900
   ```
3. **Run database migrations (if any)**
   - Use the migration helper to move SQLite data into PostgreSQL when needed:
     ```bash
     uv run python -m orcheo.tooling.postgres_migration export --output ./migration
     uv run python -m orcheo.tooling.postgres_migration import --input ./migration
     uv run python -m orcheo.tooling.postgres_migration validate --input ./migration
     ```
4. **Deploy the application**
   - **Docker image**: Build with `docker build -t orcheo-app .` and push to your registry.
   - **Fly.io example**:
     ```bash
     fly launch --no-deploy
     fly secrets set ORCHEO_POSTGRES_DSN=...
     fly deploy
     ```
  - Ensure the container command starts uvicorn: `uvicorn orcheo_backend.app:app --host 0.0.0.0 --port ${PORT}`.
5. **Health checks**
   - Expose `/docs` and `/openapi.json` for HTTP checks.
   - Use `/ws/workflow/{workflow_id}` for synthetic workflow runs during smoke tests.

**Verification**: Run `uv run pytest tests/test_persistence.py` locally with the `ORCHEO_CHECKPOINT_BACKEND=postgres` environment variable set and a reachable Postgres DSN to mirror production behavior.

_Vault note_: Managed environments should prefer KMS-integrated vaults. Configure IAM policies so only the Orcheo runtime can decrypt with the specified key.

## Kubernetes (PostgreSQL)

Reference manifests live under `deploy/kubernetes/` for running Orcheo with a
PostgreSQL backing service. Update the secret values and image tags before
applying them.

```bash
kubectl apply -k deploy/kubernetes
```

## Operational Tips

- **Secrets**: Prefer platform-specific secret managers (Fly Secrets, Railway variables, AWS Parameter Store) and never bake DSNs or vault encryption keys into images.
- **Observability**: Route application logs to structured logging (e.g., stdout + centralized collector) and enable tracing once Milestone 6 instrumentation lands.
- **Scaling**: The FastAPI app is stateless. Scale horizontally by adding replicas while pointing them at the same checkpoint database. With bundled Caddy, keep replica pools limited to one logical deployment that shares Postgres and Redis.
- **Backups**: Schedule database backups (pg_dump or managed snapshots) to protect workflow history and run states.

Use Cloudflare Tunnel when the host is not directly reachable from the internet, or when you intentionally want tunnel-managed public hostnames in front of the direct localhost ports. For reachable hosts with direct inbound ports and one shared origin, bundled Caddy is the simpler default.

These recipes will evolve as additional milestones introduce credential vaulting, trigger services, and observability pipelines.
