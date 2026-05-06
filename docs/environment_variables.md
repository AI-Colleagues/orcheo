# Environment Variables

This document catalogues every environment variable consumed by the Orcheo
project and the components that rely on them. Unless noted otherwise, backend
services read configuration via Dynaconf with the `ORCHEO_` prefix.

## Core runtime configuration (backend)

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_CHECKPOINT_BACKEND` | `sqlite` | `sqlite` or `postgres` | Selects the checkpoint persistence backend consumed by `config/loader.py`. |
| `ORCHEO_SQLITE_PATH` | `~/.orcheo/checkpoints.sqlite` | Filesystem path (absolute or `~`-expanded) | Location of the SQLite checkpoints database when `sqlite` backend is active (see `config/defaults.py`). |
| `ORCHEO_GRAPH_STORE_BACKEND` | `sqlite` | `sqlite` or `postgres` | Selects the LangGraph store backend used for graph memory/state storage (`config/loader.py`, `persistence.py`). |
| `ORCHEO_GRAPH_STORE_SQLITE_PATH` | `~/.orcheo/graph_store.sqlite` | Absolute filesystem path (must not begin with `~`) | SQLite file path used when `ORCHEO_GRAPH_STORE_BACKEND=sqlite` (`config/app_settings.py`). |
| `ORCHEO_POSTGRES_DSN` | _none_ | PostgreSQL DSN (e.g. `postgresql://user:pass@host:port/db`) | Connection string required when any backend is set to `postgres` (checkpoint, graph store, repository, chatkit, or vault; see `config/loader.py`). |
| `ORCHEO_REPOSITORY_BACKEND` | `sqlite` | `sqlite`, `postgres`, or `inmemory` | Chooses the workflow repository implementation (`config/loader.py`). |
| `ORCHEO_REPOSITORY_SQLITE_PATH` | `~/.orcheo/workflows.sqlite` | Filesystem path | Location of the workflow repository SQLite file (`config/loader.py`). |
| `ORCHEO_WORKSPACE_BACKEND` | `inmemory` | `inmemory`, `sqlite`, or `postgres` | Chooses the workspace repository implementation used for workspaces and memberships (`config/loader.py`, `app/workspace/dependencies.py`). |
| `ORCHEO_WORKSPACE_SQLITE_PATH` | `~/.orcheo/workspaces.sqlite` | Filesystem path | Location of the persistent workspace repository SQLite file when `ORCHEO_WORKSPACE_BACKEND=sqlite` (`config/loader.py`). |
| `ORCHEO_CHATKIT_BACKEND` | `sqlite` | `sqlite` or `postgres` | Selects the ChatKit persistence backend used by `chatkit/server.py`. |
| `ORCHEO_CHATKIT_SQLITE_PATH` | `~/.orcheo/chatkit.sqlite` | Filesystem path | Storage for ChatKit conversation history when using SQLite persistence (`config/loader.py` and `chatkit/server.py`). |
| `ORCHEO_CHATKIT_STORAGE_PATH` | `~/.orcheo/chatkit` | Directory path | Filesystem root for ChatKit attachments (`config/loader.py`). |
| `ORCHEO_CHATKIT_MAX_UPLOAD_SIZE_BYTES` | `5000000` | Positive integer | Maximum upload size (bytes) accepted by the ChatKit upload endpoint (`routers/chatkit.py`, `config/loader.py`). |
| `ORCHEO_CHATKIT_CDN_BASE_URL` | `https://cdn.platform.openai.com/` | HTTP(S) URL | Overrides the upstream CDN base used by the ChatKit asset proxy routes (`chatkit_asset_proxy.py`). |
| `ORCHEO_CHATKIT_RETENTION_DAYS` | `30` | Positive integer | Retention window (in days) used by the ChatKit cleanup task (`chatkit_runtime.py`). |
| `ORCHEO_CHATKIT_WIDGET_TYPES` | `["Card","ListView"]` | Comma/JSON list of widget root types | Allow-list of widget roots the ChatKit server will hydrate into thread items (`chatkit/server.py`). |
| `ORCHEO_CHATKIT_WIDGET_ACTION_TYPES` | `["submit"]` | Comma/JSON list of action types | Widget action types the ChatKit server will dispatch back to workflows (`chatkit/server.py`). |
| `ORCHEO_HOST` | `0.0.0.0` | Hostname or IP string | Network interface to bind the FastAPI app (`config/loader.py`). |
| `ORCHEO_PORT` | `8000` | Integer (1‑65535) | TCP port exposed by the FastAPI service (`config/loader.py`). |
| `ORCHEO_CORS_ALLOW_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | JSON array or comma-separated list of origins | CORS allow-list used when constructing the FastAPI middleware (`factory.py`). `orcheo install --public-ingress` sets this to the shared public HTTPS origin and keeps localhost origins when local access ports remain enabled. Tunnel or split-origin installs should set this to the public Canvas/browser origin instead of the backend API origin. |
| `ORCHEO_UPDATE_CHECK_TIMEOUT_SECONDS` | `3.0` | Float > 0 | Timeout for backend package registry lookups used by `/api/system/info` (`app/versioning.py`). |
| `ORCHEO_UPDATE_CHECK_RETRIES` | `1` | Integer ≥ 0 | Retry count for backend package registry lookups used by `/api/system/info` (`app/versioning.py`). |
| `ORCHEO_CANVAS_VERSION` | _none_ | Version string (for example `0.8.1`) | Optional current Canvas version reported by `/api/system/info` to compare with npm latest (`app/versioning.py`). |
| `ORCHEO_TRACING_EXPORTER` | `none` | `none`, `console`, or `otlp` | Selects the tracing exporter configured by `tracing/provider.py`. |
| `ORCHEO_TRACING_ENDPOINT` | _none_ | HTTP(S) URL | Optional OTLP/HTTP collector endpoint (include `/v1/traces`) consumed by `tracing/provider.py`. |
| `ORCHEO_TRACING_SERVICE_NAME` | `orcheo-backend` | String | Resource attribute attached to every span (`config/defaults.py`). |
| `ORCHEO_TRACING_SAMPLE_RATIO` | `1.0` | Float `0.0`‑`1.0` | Probability used by the trace sampler (`tracing/provider.py`). |
| `ORCHEO_TRACING_INSECURE` | `false` | Boolean (`1/0`, `true/false`, etc.) | Allows insecure OTLP connections when set to true (`tracing/provider.py`). |
| `ORCHEO_TRACING_HIGH_TOKEN_THRESHOLD` | `1000` | Positive integer | Token usage threshold that emits `token.chunk` events (`tracing/workflow.py`). |
| `ORCHEO_TRACING_PREVIEW_MAX_LENGTH` | `512` | Positive integer ≥ 16 | Maximum characters retained for prompt/response previews (`tracing/workflow.py`). |
| `ORCHEO_CHATKIT_PUBLIC_BASE_URL` | _none_ | HTTP(S) URL | Optional frontend origin used when generating ChatKit share links in the backend API responses and the CLI/MCP; defaults to `ORCHEO_API_URL` with any `/api` suffix removed when unset in the CLI/MCP (`publish.py`). One-off overrides can be supplied via `orcheo workflow publish --chatkit-public-base-url`. |

Note: `ORCHEO_REPOSITORY_BACKEND=inmemory` stores runs in-process only and does not enqueue webhook/cron/manual triggers for execution. These runs remain `PENDING` unless you execute them manually (for example, via the websocket runner).

## Canvas frontend configuration

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `VITE_ORCHEO_BACKEND_URL` | `http://localhost:8000` | HTTP(S) URL | Base URL for the Orcheo backend API used by Canvas. Public-ingress installs set this to the shared public origin (for example, `https://orcheo.example.com`). |
| `VITE_ORCHEO_AUTH_ISSUER` | _none_ | OIDC issuer URL | OIDC issuer used for IdP-only login (Canvas OAuth). |
| `VITE_ORCHEO_AUTH_CLIENT_ID` | _none_ | String | OAuth client ID registered for the Canvas SPA. |
| `VITE_ORCHEO_AUTH_REDIRECT_URI` | `${origin}/auth/callback` | URL | Redirect URI registered with the IdP (Canvas callback route). |
| `VITE_ORCHEO_AUTH_SCOPES` | `openid profile email` | Space-delimited scopes | Scopes requested during OIDC login. |
| `VITE_ORCHEO_AUTH_STATE_BYTES` | `32` | Integer in `[16, 96]` | Byte length for generated OAuth `state` random values (`features/auth/lib/oidc-client.ts`). |
| `VITE_ORCHEO_AUTH_VERIFIER_BYTES` | `64` | Integer in `[32, 96]` | Byte length for PKCE `code_verifier` random values (`features/auth/lib/oidc-client.ts`). |
| `VITE_ORCHEO_AUTH_AUDIENCE` | _none_ | String | Optional audience value required by some IdPs. |
| `VITE_ORCHEO_AUTH_ORGANIZATION` | _none_ | String | Optional organization identifier for IdPs that support organization-scoped login (e.g., Auth0 Organizations). When set, restricts login to users belonging to the specified organization. |
| `VITE_ORCHEO_AUTH_PROVIDER_PARAM` | _none_ | String | Optional IdP hint parameter name (e.g., `connection`, `idp`). |
| `VITE_ORCHEO_AUTH_PROVIDER_GOOGLE` | _none_ | String | Provider hint value for Google when `VITE_ORCHEO_AUTH_PROVIDER_PARAM` is set. |
| `VITE_ORCHEO_AUTH_PROVIDER_GITHUB` | _none_ | String | Provider hint value for GitHub when `VITE_ORCHEO_AUTH_PROVIDER_PARAM` is set. |
| `VITE_ORCHEO_CHATKIT_DOMAIN_KEY` | _none_ | String | ChatKit domain key used by Canvas public chat surfaces. Setup prompts for this value; if left unset/placeholder, ChatKit UI features remain disabled until configured. |
| `VITE_ORCHEO_CHATKIT_DEFAULT_DOMAIN_KEY` | `domain_pk_localhost_dev` | String | Dev-only fallback domain key used when neither `VITE_ORCHEO_CHATKIT_DOMAIN_KEY` nor runtime `window.__ORCHEO_CONFIG__.chatkitDomainKey` is provided (`features/chatkit/lib/chatkit-client.ts`). |
| `VITE_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames | Hostnames the Canvas server will accept requests for (maps to `server.allowedHosts` in `vite.config.ts`). Public-ingress installs append the configured public hostname. Tunnel or custom split-origin installs should include the public Canvas hostname here. |

## Vault configuration

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_VAULT_BACKEND` | `file` | `file`, `inmemory`, `aws_kms`, or `postgres` | Chooses the credential vault backend (`config/loader.py`, `config/vault_settings.py`). |
| `ORCHEO_VAULT_LOCAL_PATH` | `~/.orcheo/vault.sqlite` | Filesystem path | Location of the file-backed vault database when `file` backend is selected (`config/loader.py`). |
| `ORCHEO_VAULT_ENCRYPTION_KEY` | _none_ | String (ideally 128+ bits) | Optional pre-shared key used to encrypt secrets (required for `aws_kms`, used elsewhere when present). |
| `ORCHEO_VAULT_AWS_REGION` | _none_ | AWS region identifier (e.g. `us-east-1`) | Region targeted when `ORCHEO_VAULT_BACKEND=aws_kms` (`config/loader.py`). |
| `ORCHEO_VAULT_AWS_KMS_KEY_ID` | _none_ | KMS key identifier | Key ID for AWS KMS vaults (`config/loader.py`). |
| `ORCHEO_VAULT_TOKEN_TTL_SECONDS` | `3600` | Positive integer | Lifetime (seconds) for vault access tokens (`config/loader.py`). |
| `ORCHEO_MULTI_WORKSPACE_ENABLED` | `false` | Boolean (`1/0`, `true/false`, `yes/no`, `on/off`) | Enables workspace-aware request resolution, scoped repository lookups, and workspace-aware websocket/runnable execution (`config/loader.py`). |
| `ORCHEO_MULTI_WORKSPACE_DEFAULT_WORKSPACE_SLUG` | `default` | Slug string | Default workspace slug used when workspace scoping is disabled or when bootstrapping the shared deployment workspace (`config/loader.py`). |
| `ORCHEO_MULTI_WORKSPACE_WORKSPACE_HEADER` | `X-Orcheo-Workspace` | HTTP header name | Header that pins the active workspace for authenticated requests (`config/loader.py`). |

## ChatKit rate limits

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_CHATKIT_RATE_LIMIT_IP_LIMIT` | `120` | Integer ≥ 0 | Per-IP ChatKit request limit (`chatkit_rate_limit_settings.py`). |
| `ORCHEO_CHATKIT_RATE_LIMIT_IP_INTERVAL` | `60` | Integer > 0 | Window (seconds) used with the IP limit (`chatkit_rate_limit_settings.py`). |
| `ORCHEO_CHATKIT_RATE_LIMIT_JWT_LIMIT` | `120` | Integer ≥ 0 | Rate limit for JWT-authenticated identities (`chatkit_rate_limit_settings.py`). |
| `ORCHEO_CHATKIT_RATE_LIMIT_JWT_INTERVAL` | `60` | Integer > 0 | Window (seconds) used with the JWT identity limit (`chatkit_rate_limit_settings.py`). |
| `ORCHEO_CHATKIT_RATE_LIMIT_PUBLISH_LIMIT` | `60` | Integer ≥ 0 | Rate limit for publishing workflows via ChatKit (`chatkit_rate_limit_settings.py`). |
| `ORCHEO_CHATKIT_RATE_LIMIT_PUBLISH_INTERVAL` | `60` | Integer > 0 | Interval (seconds) for publish limits (`chatkit_rate_limit_settings.py`). |
| `ORCHEO_CHATKIT_RATE_LIMIT_SESSION_LIMIT` | `60` | Integer ≥ 0 | Rate limit for managing ChatKit sessions (`chatkit_rate_limit_settings.py`). |
| `ORCHEO_CHATKIT_RATE_LIMIT_SESSION_INTERVAL` | `60` | Integer > 0 | Interval (seconds) for session limits (`chatkit_rate_limit_settings.py`). |

## Authentication service

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_AUTH_MODE` | `optional` | `disabled`, `optional`, `required` | Controls whether authentication is disabled, allowed, or enforced (`authentication/settings.py`). |
| `ORCHEO_AUTH_JWT_SECRET` | _none_ | Arbitrary string | Symmetric key for signing/verifying JWTs (`authentication/settings.py`). |
| `ORCHEO_AUTH_JWKS_URL` | _none_ | URL returning JWKS JSON | Remote JWKS endpoint for asymmetric JWT validation (`authentication/settings.py`). |
| `ORCHEO_AUTH_JWKS` / `ORCHEO_AUTH_JWKS_STATIC` | _none_ | JSON text or mapping containing JWKS data | Inline JWKS definitions as JSON/text for offline validation (`authentication/settings.py`). |
| `ORCHEO_AUTH_JWKS_CACHE_TTL` | `300` | Integer ≥ 0 | Cache duration (seconds) for downloaded JWKS docs (`authentication/settings.py`). |
| `ORCHEO_AUTH_JWKS_TIMEOUT` | `5.0` | Float > 0 | HTTP timeout (seconds) when fetching remote JWKS (`authentication/settings.py`). |
| `ORCHEO_AUTH_ALLOWED_ALGORITHMS` | `RS256, HS256` | Comma/JSON list of JWT algorithm names | Restricts acceptable signing algorithms (`authentication/settings.py`). |
| `ORCHEO_AUTH_AUDIENCE` | _none_ | Comma/JSON list of strings | Acceptable JWT audiences (`authentication/settings.py`). |
| `ORCHEO_AUTH_ISSUER` | _none_ | String | Expected JWT issuer claim (`authentication/settings.py`). |
| `ORCHEO_AUTH_SERVICE_TOKEN_DB_PATH` | Derived from `ORCHEO_REPOSITORY_SQLITE_PATH` (defaults to `~/.orcheo/service_tokens.sqlite`) | Filesystem path | Override the service token SQLite file (`authentication/settings.py`). |
| `ORCHEO_AUTH_RATE_LIMIT_IP` | `0` | Integer ≥ 0 | Per-IP HTTP rate limit for authentication endpoints (`authentication/settings.py`). |
| `ORCHEO_AUTH_RATE_LIMIT_IDENTITY` | `0` | Integer ≥ 0 | Rate limit keyed by identity (`authentication/settings.py`). |
| `ORCHEO_AUTH_RATE_LIMIT_INTERVAL` | `60` | Integer > 0 | Interval (seconds) governing the authentication rate limits (`authentication/settings.py`). |
| `ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN` | _none_ | Token string | Temporary service token used for bootstrapping before persistent storage exists (`authentication/settings.py`). |
| `ORCHEO_AUTH_BOOTSTRAP_TOKEN_SCOPES` | `admin:tokens:read`, `admin:tokens:write`, `workflows:read`, `workflows:write`, `workflows:execute`, `vault:read`, `vault:write` | Comma/JSON list of scope strings | Scopes granted to the bootstrap token (`authentication/settings.py`). |
| `ORCHEO_AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT` | _none_ | ISO 8601 string or UNIX timestamp | Expiration to attach to the bootstrap token (`authentication/settings.py`). |
| `ORCHEO_AUTH_DEV_LOGIN_ENABLED` | `false` | Boolean (`1/0`, `true/false`, `yes/no`, `on/off`) | Enables the developer login flow for local testing (`authentication/settings.py`). |
| `ORCHEO_AUTH_DEV_COOKIE_NAME` | `orcheo_dev_session` | Cookie name string | Name of the cookie used for dev login sessions (`authentication/settings.py`). |
| `ORCHEO_AUTH_DEV_SCOPES` | `workflows:read`, `workflows:write`, `workflows:execute`, `vault:read`, `vault:write` | Comma/JSON list of scope strings | Scopes issued to dev login tokens (`authentication/settings.py`). |
| `ORCHEO_AUTH_DEV_WORKSPACE_IDS` | _none_ | Comma/JSON list of workspace IDs | Limits dev login tokens to specific workspaces (`authentication/settings.py`). |

## ChatKit session tokens

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_CHATKIT_TOKEN_SIGNING_KEY` | _none_ | String (HS or RSA private key material) | Primary signing key for ChatKit session tokens; required for ChatKit issuance (`chatkit_tokens.py`). |
| `ORCHEO_CHATKIT_TOKEN_ISSUER` | `orcheo.chatkit` | String | `iss` claim embedded into ChatKit session JWTs (`chatkit_tokens.py`). |
| `ORCHEO_CHATKIT_TOKEN_AUDIENCE` | `chatkit` | String | `aud` claim embedded into ChatKit session JWTs (`chatkit_tokens.py`). |
| `ORCHEO_CHATKIT_TOKEN_TTL_SECONDS` | `300` | Integer ≥ 60 | Expiry (seconds) for ChatKit tokens (`chatkit_tokens.py`). |
| `ORCHEO_CHATKIT_TOKEN_ALGORITHM` | `HS256` | JWT algorithm supported by PyJWT (`HS256`, `RS256`, etc.) | Algorithm used to sign ChatKit tokens (`chatkit_tokens.py`). |

## Logging & runtime flags

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_ENV` | _none_ | String (`development`, `dev`, `local`, etc.) | Preferred indicator of a developer environment when deciding to expose sensitive logs (`chatkit_runtime.py`). |
| `NODE_ENV` | `production` | String | Default runtime environment when `ORCHEO_ENV` is unset (`chatkit_runtime.py`). |
| `LOG_SENSITIVE_DEBUG` | _none_ | Set to `1` to enable; otherwise leave blank | Forces sensitive logging even outside of a recognized dev environment (`chatkit_runtime.py`). |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, etc. | Controls the logger thresholds configured in `logging_config.py`. |
| `LOG_FORMAT` | `console` | `console` or `json` | Selects structured log rendering. Any value other than `console` falls back to JSON rendering (`logging_config.py`). |

## Node integration configuration

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_MCP_STDIO_LOG` | `/tmp/orcheo-mcp-stdio.log` | Filesystem path | Log file path for stdio-based MCP transport in `SlackNode`; useful for debugging MCP integration issues (`nodes/slack.py`). |

## Celery worker configuration

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL | Broker URL for Celery task queue (`celery_app.py`). |
| `CRON_DISPATCH_INTERVAL` | `60` | Float (seconds) | Interval at which Celery Beat dispatches cron triggers (`celery_app.py`). |
| `CELERY_BEAT_SCHEDULE_FILE` | `celerybeat-schedule` | Filesystem path | Location of the Celery Beat schedule database; use `-s` flag or this env var to override (`celery_app.py`). |

## CLI configuration

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_CONFIG_DIR` | `~/.config/orcheo` | Directory path | Overrides where the CLI looks for `cli.toml` (`cli/config.py`). |
| `ORCHEO_CACHE_DIR` | `~/.cache/orcheo` | Directory path | Location for CLI caches (`cli/config.py`). |
| `ORCHEO_PROFILE` | `default` | Profile name present in `cli.toml` | Chooses which CLI profile to load (`cli/config.py`). |
| `ORCHEO_API_URL` | `http://localhost:8000` | HTTP(S) URL | URL of the Orcheo backend used by the CLI/SDK (`cli/config.py`). For Cloudflare Tunnel or other public split-origin setups, set this to the public backend hostname rather than the Canvas hostname. |
| `ORCHEO_SERVICE_TOKEN` | _none_ | Bearer token string | Service authentication token used by the CLI/SDK and emitted in generated code snippets (`cli/config.py`, `services/codegen.py`). |
| `ORCHEO_HUMAN` | _unset_ | Boolean (`1/0`, `true/false`, `yes/no`, `on/off`) | When set to a truthy value, the CLI uses human-friendly Rich output (colored tables, panels) instead of machine-readable format (JSON, Markdown tables). Equivalent to passing `--human` (`cli/main.py`). |
| `ORCHEO_DISABLE_UPDATE_CHECK` | _unset_ | Boolean (`1/0`, `true/false`, `yes/no`, `on/off`) | Disables startup update reminders in the CLI (`cli/main.py`). |
| `ORCHEO_STACK_DIR` | `~/.orcheo/stack` | Directory path | Target directory for `orcheo install` stack assets and generated `.env` updates (`cli/setup.py`). |
| `ORCHEO_STACK_VERSION` | _unset_ | Stack release version string (for example `0.1.0`) | Pins `orcheo install` to a specific `stack-v*` release when `--stack-version` is not provided (`cli/setup.py`). |
| `ORCHEO_STACK_IMAGE` | `ghcr.io/ai-colleagues/orcheo-stack:latest` | Container image reference | Runtime image used by `deploy/stack/docker-compose.yml` for backend/worker/celery-beat services. `orcheo install --stack-version` sets this value in `.env` (`cli/setup.py`). |
| `ORCHEO_POSTGRES_PASSWORD` | _auto-generated on install_ | Non-empty string | PostgreSQL password written to stack `.env` by `orcheo install` and consumed by `deploy/stack/docker-compose.yml` to configure the Postgres service and backend DSN. |
| `ORCHEO_STACK_ASSET_BASE_URL` | _unset_ | HTTP(S) URL | Optional custom mirror base URL for per-file stack asset downloads. When set, `orcheo install` skips GitHub tag discovery and downloads stack assets from this mirror (`cli/setup.py`). |
| `ORCHEO_SETUP_HEALTH_POLL_TIMEOUT_SECONDS` | `60` | Integer ≥ 0 | Timeout window used by `orcheo install` when waiting for `docker compose` backend health checks (`cli/setup.py`). |
| `ORCHEO_PUBLIC_INGRESS_ENABLED` | `false` | Boolean (`1/0`, `true/false`, `yes/no`, `on/off`) | Enables the bundled Caddy ingress profile written by `orcheo install`. When false, backend and canvas are accessible only via their direct localhost port bindings. |
| `ORCHEO_PUBLIC_HOST` | _unset_ | Hostname | Public hostname served by bundled Caddy. Required when `ORCHEO_PUBLIC_INGRESS_ENABLED=true`. |
| `COMPOSE_PROFILES` | _empty_ | Comma-separated Docker Compose profile names | Profiles activated by `orcheo install` and `orcheo stack`. Set to `public-ingress` to enable bundled Caddy TLS ingress. |
| `ORCHEO_CADDY_SITE_ADDRESS` | _unset_ | Hostname or Caddy site address | Site address consumed by `deploy/stack/Caddyfile`. Usually the same value as `ORCHEO_PUBLIC_HOST`. |
| `ORCHEO_CADDY_BACKEND_UPSTREAMS` | `backend:8000` | Space-delimited `host:port` upstream list | Backend upstream pool used by bundled Caddy for `/api/*` and `/ws/*`. Multiple entries are for replicas of the same logical deployment only. |
| `ORCHEO_CADDY_CANVAS_UPSTREAM` | `canvas:5173` | `host:port` | Internal Canvas upstream used by bundled Caddy for `/` and SPA routes. |
| `ORCHEO_CADDY_HTTP_BIND` | `0.0.0.0` | IP string | Host bind address for Caddy's public port `80` in `deploy/stack/docker-compose.yml`. |
| `ORCHEO_CADDY_HTTPS_BIND` | `0.0.0.0` | IP string | Host bind address for Caddy's public port `443` in `deploy/stack/docker-compose.yml`. |
| `ORCHEO_BACKEND_LOCAL_PORT` | `8000` | Integer (1‑65535) | Localhost port bound for the backend service in the stack compose file. |
| `ORCHEO_CANVAS_LOCAL_PORT` | `5173` | Integer (1‑65535) | Localhost port bound for the Canvas service in the stack compose file. |
| `ORCHEO_POSTGRES_LOCAL_PORT` | `5432` | Integer (1‑65535) | Localhost port bound for the bundled Postgres service in the stack compose file. |
| `ORCHEO_REDIS_LOCAL_PORT` | `6379` | Integer (1‑65535) | Localhost port bound for the bundled Redis service in the stack compose file. |
| `ORCHEO_AUTH_ISSUER` | _none_ | OIDC issuer URL | OAuth issuer URL for CLI browser-based login. Can also be set in a `cli.toml` profile via `auth_issuer` (`cli/auth/config.py`). |
| `ORCHEO_AUTH_CLIENT_ID` | _none_ | String | OAuth client ID for CLI login. Can also be set in a `cli.toml` profile via `auth_client_id` (`cli/auth/config.py`). |
| `ORCHEO_AUTH_SCOPES` | `openid profile email` | Space-delimited scopes | OAuth scopes requested during CLI login. Can also be set in a `cli.toml` profile via `auth_scopes` (`cli/auth/config.py`). |
| `ORCHEO_AUTH_AUDIENCE` | _none_ | String | Optional OAuth audience for CLI login. Can also be set in a `cli.toml` profile via `auth_audience` (`cli/auth/config.py`). |
| `ORCHEO_AUTH_ORGANIZATION` | _none_ | String | Optional OAuth organization for CLI login (e.g., Auth0 Organizations). Can also be set in a `cli.toml` profile via `auth_organization` (`cli/auth/config.py`). |
