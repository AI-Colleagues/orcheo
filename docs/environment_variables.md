# Environment Variables

This document catalogues every environment variable consumed by the Orcheo
project and the components that rely on them. Unless noted otherwise, backend
services read configuration via Dynaconf with the `ORCHEO_` prefix.

## Core runtime configuration (backend)

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_CHECKPOINT_BACKEND` | `postgres` | `postgres` | Selects the checkpoint persistence backend consumed by `config/loader.py`. |
| `ORCHEO_GRAPH_STORE_BACKEND` | `postgres` | `postgres` | Selects the LangGraph store backend used for graph memory/state storage (`config/loader.py`, `persistence.py`). |
| `ORCHEO_POSTGRES_DSN` | _none_ | PostgreSQL DSN (e.g. `postgresql://user:pass@host:port/db`) | Connection string required when any backend is set to `postgres` (checkpoint, graph store, repository, workspace, auth service tokens, chatkit, or vault; see `config/loader.py`). |
| `ORCHEO_REPOSITORY_BACKEND` | `postgres` | `postgres` | Chooses the workflow repository implementation (`config/loader.py`). |
| `ORCHEO_WORKSPACE_BACKEND` | `postgres` | `postgres` | Chooses the workspace repository implementation used for workspaces and memberships (`config/loader.py`, `app/workspace/dependencies.py`). |
| `ORCHEO_CHATKIT_BACKEND` | `postgres` | `postgres` | Selects the ChatKit persistence backend used by `chatkit/server.py`. |
| `ORCHEO_CHATKIT_STORAGE_PATH` | `~/.orcheo/chatkit` | Directory path | Filesystem root for ChatKit attachments (`config/loader.py`). |
| `ORCHEO_CHATKIT_MAX_UPLOAD_SIZE_BYTES` | `5000000` | Positive integer | Maximum upload size (bytes) accepted by the ChatKit upload endpoint (`routers/chatkit.py`, `config/loader.py`). |
| `ORCHEO_CHATKIT_CDN_BASE_URL` | `https://cdn.platform.openai.com/` | HTTP(S) URL | Overrides the upstream CDN base used by the ChatKit asset proxy routes (`chatkit_asset_proxy.py`). |
| `ORCHEO_CHATKIT_RETENTION_DAYS` | `30` | Positive integer | Retention window (in days) used by the ChatKit cleanup task (`chatkit_runtime.py`). |
| `ORCHEO_CHATKIT_WIDGET_TYPES` | `["Card","ListView"]` | Comma/JSON list of widget root types | Allow-list of widget roots the ChatKit server will hydrate into thread items (`chatkit/server.py`). |
| `ORCHEO_CHATKIT_WIDGET_ACTION_TYPES` | `["submit"]` | Comma/JSON list of action types | Widget action types the ChatKit server will dispatch back to workflows (`chatkit/server.py`). |
| `ORCHEO_HOST` | `0.0.0.0` | Hostname or IP string | Network interface to bind the FastAPI app (`config/loader.py`). |
| `ORCHEO_PORT` | `2025` | Integer (1‑65535) | TCP port exposed by the FastAPI service (`config/loader.py`). |
| `ORCHEO_CORS_ALLOW_ORIGINS` | `["http://localhost:2026","http://127.0.0.1:2026"]` | JSON array or comma-separated list of origins | CORS allow-list used when constructing the FastAPI middleware (`factory.py`). `orcheo install --public-ingress` sets this to the shared public HTTPS origin and keeps localhost origins when local access ports remain enabled. Tunnel or split-origin installs should set this to the public Studio/browser origin instead of the backend API origin. |
| `ORCHEO_UPDATE_CHECK_TIMEOUT_SECONDS` | `3.0` | Float > 0 | Timeout for backend package registry lookups used by `/api/system/info` (`app/versioning.py`). |
| `ORCHEO_UPDATE_CHECK_RETRIES` | `1` | Integer ≥ 0 | Retry count for backend package registry lookups used by `/api/system/info` (`app/versioning.py`). |
| `ORCHEO_STUDIO_VERSION` | _none_ | Version string (for example `0.8.1`) | Optional current Studio version reported by `/api/system/info` to compare with npm latest (`app/versioning.py`). |
| `ORCHEO_TRACING_EXPORTER` | `none` | `none`, `console`, or `otlp` | Selects the tracing exporter configured by `tracing/provider.py`. |
| `ORCHEO_TRACING_ENDPOINT` | _none_ | HTTP(S) URL | Optional OTLP/HTTP collector endpoint (include `/v1/traces`) consumed by `tracing/provider.py`. |
| `ORCHEO_TRACING_SERVICE_NAME` | `orcheo-backend` | String | Resource attribute attached to every span (`config/defaults.py`). |
| `ORCHEO_TRACING_SAMPLE_RATIO` | `1.0` | Float `0.0`‑`1.0` | Probability used by the trace sampler (`tracing/provider.py`). |
| `ORCHEO_TRACING_INSECURE` | `false` | Boolean (`1/0`, `true/false`, etc.) | Allows insecure OTLP connections when set to true (`tracing/provider.py`). |
| `ORCHEO_TRACING_HIGH_TOKEN_THRESHOLD` | `1000` | Positive integer | Token usage threshold that emits `token.chunk` events (`tracing/workflow.py`). |
| `ORCHEO_TRACING_PREVIEW_MAX_LENGTH` | `512` | Positive integer ≥ 16 | Maximum characters retained for prompt/response previews (`tracing/workflow.py`). |
| `ORCHEO_WORKFLOW_DEFINITION_MODE` | `unrestricted` | `restricted` or `unrestricted` | Selects how uploaded `workflow.py` files are ingested and executed. `restricted` compiles every upload to a frozen IR (no author code runs at ingestion) and runs `CodeNode` bodies in the MicroPython-WASM sandbox; `unrestricted` keeps today's in-process `load_graph_from_script` path and is **not tenant-safe**. The runtime default is `unrestricted`; `orcheo install` writes `restricted` for a trusted HTTPS backend and `unrestricted` for local-hosting or untrusted-HTTP deployments. See [Sandboxed Custom Workflows](sandboxed_custom_workflows.md) (`config/loader.py`, `graph/ir/definition_mode.py`, `cli/setup.py`). |
| `ORCHEO_CHATKIT_PUBLIC_BASE_URL` | _none_ | HTTP(S) URL | Optional frontend origin used when generating ChatKit share links in the backend API responses and the CLI/MCP; defaults to `ORCHEO_API_URL` with any `/api` suffix removed when unset in the CLI/MCP (`publish.py`). One-off overrides can be supplied via `orcheo workflow publish --studio-url`. |
| `ORCHEO_CHATKIT_ATTACHMENT_BASE_URL` | `http://localhost:2025` in compose stacks | HTTP URL | Base URL used by ChatKit attachment helpers to resolve bytes and upload content against the backend. Leave unset to fall back to `ORCHEO_API_URL`/`ORCHEO_API_BASE_URL`. |
| `ORCHEO_CANDIDATES_REPO` | `AI-Colleagues/colleague-candidates` | GitHub `owner/repo` | Candidate colleague catalog fetched by `/api/candidates` (`app/candidates_service.py`). |
| `ORCHEO_CANDIDATES_REPO_REF` | `main` | Branch, tag, or commit ref | Candidate catalog ref fetched by `/api/candidates`; set this to a staging branch such as `test-updating` to validate candidate metadata and update notes before merging (`app/candidates_service.py`). |
| `ORCHEO_CANDIDATES_GITHUB_TOKEN` | _none_ | GitHub token | Optional token used when fetching the candidate catalog tarball, useful for private repos or higher GitHub API limits (`app/candidates_service.py`). |

## Studio frontend configuration

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `VITE_ORCHEO_BACKEND_URL` | `http://localhost:2025` | HTTP(S) URL | Base URL for the Orcheo backend API used by Studio. Public-ingress installs set this to the shared public origin (for example, `https://orcheo.example.com`). |
| `VITE_ORCHEO_AUTH_DISABLED` | `false` | Boolean (`true`/`false`) | When `true`, the Studio login gate is bypassed entirely — for self-host/dev deployments running the backend with `ORCHEO_AUTH_MODE=disabled`. Leave unset/`false` in production so the first-party email login screen is required. |
| `VITE_ORCHEO_CHATKIT_DOMAIN_KEY` | _none_ | String | ChatKit domain key used by Studio public chat surfaces. Setup prompts for this value; if left unset/placeholder, ChatKit UI features remain disabled until configured. |
| `VITE_ORCHEO_CHATKIT_DEFAULT_DOMAIN_KEY` | `domain_pk_localhost_dev` | String | Dev-only fallback domain key used when neither `VITE_ORCHEO_CHATKIT_DOMAIN_KEY` nor runtime `window.__ORCHEO_CONFIG__.chatkitDomainKey` is provided (`features/chatkit/lib/chatkit-client.ts`). |
| `VITE_ORCHEO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames | Hostnames the Studio server will accept requests for (maps to `server.allowedHosts` in `vite.config.ts`). Public-ingress installs append the configured public hostname. Tunnel or custom split-origin installs should include the public Studio hostname here. |

## Vault configuration

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_VAULT_BACKEND` | `postgres` | `postgres` | Chooses the credential vault backend (`config/loader.py`, `config/vault_settings.py`). |
| `ORCHEO_VAULT_ENCRYPTION_KEY` | _none_ | String (ideally 128+ bits) | Pre-shared key required when `ORCHEO_VAULT_BACKEND=postgres`. |
| `ORCHEO_VAULT_TOKEN_TTL_SECONDS` | `3600` | Positive integer | Lifetime (seconds) for vault access tokens (`config/loader.py`). |
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
| `ORCHEO_AUTH_JWT_SECRET` | _none_ | Arbitrary string | First-party HS256 signing key for the passwordless email IdP — signs and verifies access tokens. **Required when `ORCHEO_AUTH_MODE=required`.** `orcheo install` auto-generates it for required-auth stacks; otherwise generate with e.g. `openssl rand -hex 32` (`authentication/settings.py`). |
| `ORCHEO_AUTH_ACCESS_TOKEN_TTL_SECONDS` | `900` | Integer > 0 | Lifetime of issued first-party access tokens (identity service). |
| `ORCHEO_AUTH_CHALLENGE_TTL_MINUTES` | `15` | Integer > 0 | Lifetime of a magic-link/OTP email challenge (identity service). |
| `ORCHEO_AUTH_SESSION_TTL_DAYS` | `30` | Integer > 0 | Lifetime of a refresh-token session (identity service). |
| `ORCHEO_AUTH_OTP_DIGITS` | `6` | Integer ≥ 4 | Number of digits in the emailed OTP code (identity service). |
| `ORCHEO_AUTH_OTP_MAX_ATTEMPTS` | `5` | Integer > 0 | OTP attempts before a challenge is locked out (identity service). |
| `ORCHEO_AUTH_JWKS_URL` | _none_ | URL returning JWKS JSON | **Dormant.** Generic external-issuer JWKS endpoint, retained for the future enterprise-SSO initiative; unset for first-party auth (`authentication/settings.py`). |
| `ORCHEO_AUTH_JWKS` / `ORCHEO_AUTH_JWKS_STATIC` | _none_ | JSON text or mapping containing JWKS data | Inline JWKS definitions as JSON/text for offline validation (`authentication/settings.py`). |
| `ORCHEO_AUTH_JWKS_CACHE_TTL` | `300` | Integer ≥ 0 | Cache duration (seconds) for downloaded JWKS docs (`authentication/settings.py`). |
| `ORCHEO_AUTH_JWKS_TIMEOUT` | `5.0` | Float > 0 | HTTP timeout (seconds) when fetching remote JWKS (`authentication/settings.py`). |
| `ORCHEO_AUTH_ALLOWED_ALGORITHMS` | `RS256, HS256` | Comma/JSON list of JWT algorithm names | Restricts acceptable signing algorithms (`authentication/settings.py`). |
| `ORCHEO_AUTH_AUDIENCE` | _none_ | Comma/JSON list of strings | Audience embedded in first-party tokens and validated by the backend (e.g. `orcheo-api`) (`authentication/settings.py`). |
| `ORCHEO_AUTH_ISSUER` | _none_ | String | First-party token issuer; the backend accepts only this issuer (e.g. `https://auth.orcheo.cloud`) (`authentication/settings.py`). |
| `ORCHEO_AUTH_SERVICE_TOKEN_DB_PATH` | _none_ | Filesystem path | Override the service token store path when needed (`authentication/settings.py`). |
| `ORCHEO_AUTH_RATE_LIMIT_IP` | `0` | Integer ≥ 0 | Per-IP HTTP rate limit for authentication endpoints (`authentication/settings.py`). |
| `ORCHEO_AUTH_RATE_LIMIT_IDENTITY` | `0` | Integer ≥ 0 | Rate limit keyed by identity (`authentication/settings.py`). |
| `ORCHEO_AUTH_RATE_LIMIT_INTERVAL` | `60` | Integer > 0 | Interval (seconds) governing the authentication rate limits (`authentication/settings.py`). |
| `ORCHEO_TRUSTED_PROXY` | `false` | Boolean (`1/0`, `true/false`, etc.) | When true, passwordless auth start rate limiting uses the first `X-Forwarded-For` entry as the client IP. Enable only when the backend is reachable exclusively through a trusted reverse proxy that overwrites this header (`identity/dependencies.py`). |
| `ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN` | _none_ | Token string | Temporary service token used for bootstrapping before persistent storage exists (`authentication/settings.py`). |
| `ORCHEO_AUTH_BOOTSTRAP_TOKEN_SCOPES` | `admin:tokens:read`, `admin:tokens:write`, `workflows:read`, `workflows:write`, `workflows:execute`, `vault:read`, `vault:write` | Comma/JSON list of scope strings | Scopes granted to the bootstrap token (`authentication/settings.py`). |
| `ORCHEO_AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT` | _none_ | ISO 8601 string or UNIX timestamp | Expiration to attach to the bootstrap token (`authentication/settings.py`). |
| `ORCHEO_AUTH_DEV_LOGIN_ENABLED` | `false` | Boolean (`1/0`, `true/false`, `yes/no`, `on/off`) | Enables the developer login flow for local testing (`authentication/settings.py`). |
| `ORCHEO_AUTH_DEV_COOKIE_NAME` | `orcheo_dev_session` | Cookie name string | Name of the cookie used for dev login sessions (`authentication/settings.py`). |
| `ORCHEO_AUTH_DEV_SCOPES` | `workflows:read`, `workflows:write`, `workflows:execute`, `vault:read`, `vault:write` | Comma/JSON list of scope strings | Scopes issued to dev login tokens (`authentication/settings.py`). |
| `ORCHEO_AUTH_DEV_WORKSPACE_IDS` | _none_ | Comma/JSON list of workspace IDs | Limits dev login tokens to specific workspaces (`authentication/settings.py`). |

## Transactional email (SMTP)

SMTP is the sole production transport for both passwordless auth challenges
(sign-in links/codes) and workspace invitation emails. When `ORCHEO_SMTP_HOST`
is unset, the backend logs the link/code instead of delivering email (the
self-host/dev default).

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_SMTP_HOST` | _none_ | Hostname | SMTP server host. Unset → log links/codes instead of sending (`email_config.py`). |
| `ORCHEO_SMTP_PORT` | `587` | Integer | SMTP server port (`email_config.py`). |
| `ORCHEO_SMTP_USERNAME` | _none_ | String | SMTP auth username (`email_config.py`). |
| `ORCHEO_SMTP_PASSWORD` | _none_ | String | SMTP auth password (`email_config.py`). |
| `ORCHEO_SMTP_FROM_EMAIL` | `no-reply@orcheo.cloud` | Email address | From-address for all transactional email; use a domain you control (`email_config.py`). |
| `ORCHEO_SMTP_USE_TLS` | `true` | Boolean | Use STARTTLS for the SMTP connection (`email_config.py`). |

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
| `NODE_ENV` | `production` | String | Standard runtime environment fallback when `ORCHEO_ENV` is unset (`chatkit_runtime.py`). |
| `ORCHEO_LOG_SENSITIVE_DEBUG` | _none_ | Set to `1` to enable; otherwise leave blank | Forces sensitive logging even outside of a recognized dev environment (`chatkit_runtime.py`). |
| `ORCHEO_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, etc. | Controls the logger thresholds configured in `logging_config.py`. |
| `ORCHEO_LOG_FORMAT` | `console` | `console` or `json` | Selects structured log rendering. Any value other than `console` falls back to JSON rendering (`logging_config.py`). |

## Node integration configuration

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_MCP_STDIO_LOG` | `/tmp/orcheo-mcp-stdio.log` | Filesystem path | Log file path for stdio-based MCP transport in `SlackNode`; useful for debugging MCP integration issues (`nodes/slack.py`). |

## Workflow execution

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_WORKFLOW_TRUST_MODE` | `managed` (set to `allow_client_uploads` by `orcheo install` for local hosting and trusted HTTPS backends) | `allow_client_uploads` or `managed` | Controls whether client-supplied workflow scripts may be ingested. When set to `allow_client_uploads`, the Upload and Update buttons are enabled in Studio and the CLI `workflow upload` command is accepted by the backend. When set to `managed` (or any other value, which is the backend's built-in default when the variable is unset), client uploads are rejected with HTTP 403 and the upload/update UI is hidden; only server-side candidate onboarding via `POST /candidates/onboard` is permitted. `orcheo install` writes this variable into the stack `.env` based on the deployment topology: a local-hosting install (no bundled public ingress and a loopback `http://` backend) gets `allow_client_uploads` with `unrestricted` definition mode; a trusted HTTPS backend gets `allow_client_uploads` paired with `restricted` definition mode (uploads compile to the sandboxed IR); any other (untrusted non-loopback `http://`) deployment is pinned to `managed`. Set to `allow_client_uploads` with `unrestricted` definition mode only on instances where every workflow author is trusted (`graph/ingestion/sandbox.py`, `app/routers/workflows.py`, `cli/setup.py`). |

## Celery worker configuration

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL | Broker URL for Celery task queue (`celery_app.py`). |
| `ORCHEO_CRON_DISPATCH_INTERVAL` | `60` | Float (seconds) | Interval at which Celery Beat dispatches cron triggers (`celery_app.py`). |
| `ORCHEO_CELERY_BEAT_SCHEDULE_FILE` | `celerybeat-schedule` | Filesystem path | Location of the Celery Beat schedule database; use `-s` flag or this env var to override (`celery_app.py`). |

## CLI configuration

| Variable | Default | Valid values | Purpose |
| --- | --- | --- | --- |
| `ORCHEO_CONFIG_DIR` | `~/.config/orcheo` | Directory path | Overrides where the CLI looks for `cli.toml` (`cli/config.py`). |
| `ORCHEO_CACHE_DIR` | `~/.cache/orcheo` | Directory path | Location for CLI caches (`cli/config.py`). |
| `ORCHEO_PROFILE` | `default` | Profile name present in `cli.toml` | Chooses which CLI profile to load (`cli/config.py`). |
| `ORCHEO_API_URL` | `http://localhost:2025` | HTTP(S) URL | URL of the Orcheo backend used by the CLI/SDK (`cli/config.py`). For Cloudflare Tunnel or other public split-origin setups, set this to the public backend hostname rather than the Studio hostname. |
| `ORCHEO_SERVICE_TOKEN` | _none_ | Bearer token string | Service authentication token used by the CLI/SDK and emitted in generated code snippets (`cli/config.py`, `services/codegen.py`). |
| `ORCHEO_HUMAN` | _unset_ | Boolean (`1/0`, `true/false`, `yes/no`, `on/off`) | When set to a truthy value, the CLI uses human-friendly Rich output (colored tables, panels) instead of machine-readable format (JSON, Markdown tables). Equivalent to passing `--human` (`cli/main.py`). |
| `ORCHEO_DISABLE_UPDATE_CHECK` | _unset_ | Boolean (`1/0`, `true/false`, `yes/no`, `on/off`) | Disables startup update reminders in the CLI (`cli/main.py`). |
| `ORCHEO_STACK_DIR` | `~/.orcheo/stack` | Directory path | Target directory for `orcheo install` stack assets and generated `.env` updates (`cli/setup.py`). |
| `ORCHEO_STACK_VERSION` | _unset_ | Stack release version string (for example `0.1.0`) | Pins `orcheo install` to a specific `stack-v*` release when `--stack-version` is not provided (`cli/setup.py`). |
| `ORCHEO_STACK_IMAGE` | `ghcr.io/ai-colleagues/orcheo-stack:latest` | Container image reference | Runtime image used by `deploy/stack/docker-compose.yml` for backend/worker/celery-beat services. `orcheo install --stack-version` sets this value in `.env` (`cli/setup.py`). |
| `ORCHEO_POSTGRES_PASSWORD` | _auto-generated on install_ | Non-empty string | PostgreSQL password written to stack `.env` by `orcheo install` and consumed by `deploy/stack/docker-compose.yml` to configure the Postgres service and backend DSN. |
| `ORCHEO_STACK_ASSET_BASE_URL` | _unset_ | HTTP(S) URL | Optional custom mirror base URL for per-file stack asset downloads. When set, `orcheo install` skips GitHub tag discovery and downloads stack assets from this mirror (`cli/setup.py`). |
| `ORCHEO_SETUP_HEALTH_POLL_TIMEOUT_SECONDS` | `60` | Integer ≥ 0 | Timeout window used by `orcheo install` when waiting for `docker compose` backend health checks (`cli/setup.py`). |
| `ORCHEO_PUBLIC_INGRESS_ENABLED` | `false` | Boolean (`1/0`, `true/false`, `yes/no`, `on/off`) | Enables the bundled Caddy ingress profile written by `orcheo install`. When false, backend and studio are accessible only via their direct localhost port bindings. |
| `ORCHEO_PUBLIC_HOST` | _unset_ | Hostname | Public hostname served by bundled Caddy. Required when `ORCHEO_PUBLIC_INGRESS_ENABLED=true`. |
| `COMPOSE_PROFILES` | _empty_ | Comma-separated Docker Compose profile names | Profiles activated by `orcheo install` and `orcheo stack`. Set to `public-ingress` to enable bundled Caddy TLS ingress. |
| `ORCHEO_CADDY_SITE_ADDRESS` | _unset_ | Hostname or Caddy site address | Site address consumed by `deploy/stack/Caddyfile`. Usually the same value as `ORCHEO_PUBLIC_HOST`. |
| `ORCHEO_CADDY_BACKEND_UPSTREAMS` | `backend:2025` | Space-delimited `host:port` upstream list | Backend upstream pool used by bundled Caddy for `/api/*` and `/ws/*`. Multiple entries are for replicas of the same logical deployment only. |
| `ORCHEO_CADDY_STUDIO_UPSTREAM` | `studio:2026` | `host:port` | Internal Studio upstream used by bundled Caddy for `/` and SPA routes. |
| `ORCHEO_CADDY_HTTP_BIND` | `0.0.0.0` | IP string | Host bind address for Caddy's public port `80` in `deploy/stack/docker-compose.yml`. |
| `ORCHEO_CADDY_HTTPS_BIND` | `0.0.0.0` | IP string | Host bind address for Caddy's public port `443` in `deploy/stack/docker-compose.yml`. |
| `ORCHEO_BACKEND_LOCAL_PORT` | `2025` | Integer (1‑65535) | Localhost port bound for the backend service in the stack compose file. |
| `ORCHEO_STUDIO_LOCAL_PORT` | `2026` | Integer (1‑65535) | Localhost port bound for the Studio service in the stack compose file. |
| `ORCHEO_POSTGRES_LOCAL_PORT` | `5432` | Integer (1‑65535) | Localhost port bound for the bundled Postgres service in the stack compose file. |
| `ORCHEO_REDIS_LOCAL_PORT` | `6379` | Integer (1‑65535) | Localhost port bound for the bundled Redis service in the stack compose file. |
| `ORCHEO_AUTH_ISSUER` | _none_ | OIDC issuer URL | OAuth issuer URL for CLI browser-based login. Can also be set in a `cli.toml` profile via `auth_issuer` (`cli/auth/config.py`). |
| `ORCHEO_AUTH_CLIENT_ID` | _none_ | String | OAuth client ID for CLI login. Can also be set in a `cli.toml` profile via `auth_client_id` (`cli/auth/config.py`). |
| `ORCHEO_AUTH_SCOPES` | `openid profile email` | Space-delimited scopes | OAuth scopes requested during CLI login. Can also be set in a `cli.toml` profile via `auth_scopes` (`cli/auth/config.py`). |
| `ORCHEO_AUTH_AUDIENCE` | _none_ | String | Optional OAuth audience for CLI login. Can also be set in a `cli.toml` profile via `auth_audience` (`cli/auth/config.py`). |
| `ORCHEO_AUTH_ORGANIZATION` | _none_ | String | Optional OAuth organization for CLI login (e.g., Auth0 Organizations). Can also be set in a `cli.toml` profile via `auth_organization` (`cli/auth/config.py`). |
