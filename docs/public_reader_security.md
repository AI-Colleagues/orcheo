# Public event reader security

Author: Codex
Owner: ShaojieJiang

The event-link crawler handles URLs and page content supplied by strangers.
Trusted workflow authors do not make that content trusted. Its protections are
independent of `ORCHEO_WORKFLOW_DEFINITION_MODE`.

## Connection-time checks

`orcheo.security.public_https_client_kwargs()` supplies an HTTPX client that
accepts only HTTPS on port 443, rejects private and special addresses, and
disables environment proxies. Its httpcore network backend resolves the
hostname, checks **every** returned address, and connects to a checked numeric
IP. TLS verifies the original hostname and the HTTP Host header is preserved.
Every redirect is checked. IPv4-mapped addresses, multicast, and IPv6
translation/tunnel ranges cannot bypass the checks.

The restricted-mode HTTP client also pins its connections. Its existing
HTTP/HTTPS policy stays intact. Unrestricted integrations can still connect to
their databases and private services; use the public helper for untrusted URLs.

The crawler uses this helper for image downloads. A security rejection drops
the source image instead of preserving a dangerous URL in the listing. R2
uploads and PostgreSQL writes use their separate credentialed clients.

## Browser isolation in the lean stack

The worker connects to `public-browser` over Playwright's remote protocol.
The browser container does not inherit `.env`, the vault, worker home, or any
persistent volume. It runs without root, with a read-only filesystem and no
capabilities. Its two networks are internal-only: one for worker control, one
for the public proxy. Neither includes the backend or Redis. Playwright never
exposes the worker's network to the browser.

`public-egress` accepts only HTTPS CONNECT on port 443 and pins public IPs.
Before dropping root, it applies IPv4 and IPv6 firewall rules: deny private and
special destinations, permit public TCP 443, permit replies, and permit Docker
DNS. If applying either firewall fails, the proxy does not start. The proxy
has no worker credentials or shared volumes. Only it joins an internet-routed
network. The worker retains its own network for authorized DB/storage work.

Configure `ORCHEO_PUBLIC_BROWSER_WS_ENDPOINT` for other deployments using the
same isolated topology. With an endpoint configured, a connection failure never
falls back to a local browser. Without it, browser nodes retain the existing
local public-IP proxy for development; that is not process isolation.

## Tool-free extraction

The generic crawler path makes a structured model call using captured text,
HTML and metadata. It has no agent loop, shell, browser tools, or filesystem
access. Captured content is a user message; instructions are a system message.
Only the model API credential is used for this request, never the DB/R2 keys.
Schema validation and the existing full-description/agenda checks run before
facts are uploaded or stored. Provider errors return their type without error
messages that could disclose credentials. User confirmation is still required
before publishing.

The workflow now needs `openai_api_key` in the Orcheo vault (or the provider's
normal API configuration). Coding-agent subscription login is no longer used.
Its settings are `extraction_model` and `extraction_timeout_seconds`, replacing
`codex_model` and `codex_timeout_seconds`. Re-upload the workflow and its updated
configuration; migrate any saved overrides to the new names.

## Verify before enabling GatherEasy crawls

Build/release the updated lean image and install its updated Compose assets.
The worker and browser must use the same image: Playwright client/server
versions must match. Old published images do not acquire these protections
from source changes.

Run in the updated lean stack:

```sh
orcheo stack --ps
docker compose -f ~/.orcheo/lean/docker-compose.yml exec worker \
  python -m orcheo.security.reader_preflight
```

The preflight visits a public HTTPS page and checks private IPv4/IPv6 targets,
HTTP downgrade, a redirect to loopback, page-script requests, and the public
HTTP client's private-address rejection. It does not invoke the model, write
events, or upload images. Also verify that the browser's direct internet route
is blocked, the proxy firewall is installed, and the reader containers have no
credential mounts or inherited env file. The tests under `tests/security/`
exercise changing DNS answers, mixed answers, TLS hostname preservation,
environment-proxy bypasses, and deployment boundaries without private network
traffic.

Keep `GATHEREZ_ORCHEO_CRAWLER_EGRESS_GUARDED` off until the deployed image,
workflow, credentials and these checks are verified. The flag remains an
operator assertion, not automatic proof of deployment safety.
