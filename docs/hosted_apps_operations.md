# Hosted Apps operator runbook

Hosted Apps is enabled by default for installs whose resolved backend URL is loopback.
Installs with a non-local backend explicitly prompt the operator to opt in. Apps serve
traffic only when the workspace is allowlisted when an allowlist is configured and all
required domain, storage, gateway, TLS, proxy, and runtime-generation settings validate.
Setting `ORCHEO_HOSTED_APPS_ENABLED=false` retains metadata and objects while disabling
access.

`orcheo install` runs the Hosted Apps preflight before starting Compose. It validates
the base domain and wildcard DNS probe, TLS certificate paths, dedicated gateway secret,
trusted proxy CIDRs/hops, Postgres runtime state, validation queue, and bundle-storage
configuration. Run `uv run orcheo-hosted-apps-preflight` for later manual checks.

## Guided production install

When the resolved backend URL is not loopback, interactive `orcheo install` asks whether
to enable Hosted Apps. When enabled, it prompts for:

- the bare wildcard base domain, preferably the DNS zone apex such as `example.com`;
- trusted proxy CIDRs and a fixed forwarding-hop count.

Hosted Apps defaults to enabled. The workspace allowlist is not prompted; pass
`--hosted-apps-workspace-allowlist` when a staged rollout is required.
TLS paths are not prompted. New non-local installs use
`~/.orcheo/tls/apps-origin.pem` and
`~/.orcheo/tls/apps-origin-key.pem`; CLI options and existing values override
those defaults.

The installer generates the gateway identity, copies the certificate and key into the
managed stack with restricted permissions, writes the Caddy TLS directive, validates
wildcard DNS, and only then starts Compose.

For non-interactive provisioning, provide the production values explicitly:

```bash
orcheo install --yes --public-ingress \
  --public-host orcheo.example.com \
  --hosted-apps \
  --apps-base-domain example.com \
  --app-trusted-proxy-cidrs 172.16.0.0/12 \
  --app-trusted-proxy-hops 1 \
  --start-stack
```

Before using `--start-stack`, both the Studio/API hostname and a probe hostname beneath
the app wildcard domain must resolve to the installation host.

## Network and service topology

The trusted Studio/API hostname continues routing to Studio/backend. Exactly one wildcard
label under `ORCHEO_APPS_BASE_DOMAIN` routes to `app-gateway:2030`. A wildcard certificate
matches `*.example.test`, not deeper names such as `a.b.example.test`.

When Cloudflare manages the zone, prefer the zone apex as the base domain. For
example, `ORCHEO_APPS_BASE_DOMAIN=orcheo.cloud` produces
`app-test-dashboard.orcheo.cloud`, which is covered by free Universal SSL.
Using `app-test.orcheo.cloud` as the base instead produces the deeper hostname
`dashboard.app-test.orcheo.cloud`, which requires separate Cloudflare edge
certificate coverage.

Configure trusted proxy CIDRs/hops explicitly. The gateway strips browser Authorization,
workspace, workflow, actor, forwarding, and internal-service headers. Backend internal
routes accept only the dedicated gateway identity and are excluded from public OpenAPI.

Normal Compose startup includes the gateway, cleanup process, and a dedicated
`hosted-app-validation` Celery consumer with concurrency one. The global feature flag
still fails closed until configuration passes. Validation must never share the
workflow-worker queue. Monitor queue lag, object-store latency, and orphan cleanup.

## Storage

The bundled installer persists staged archives, validated manifests, and immutable app
assets in PostgreSQL. Consequently a normal database backup and restore carries hosted
app packages to a replacement machine. On upgrade, the backend idempotently imports
objects from the legacy `orcheo_app_bundles` filesystem volume before serving them from
PostgreSQL; keep that volume until the upgraded stack has completed migration.

The filesystem backend remains available for development. S3 store primitives also
exist, but the production presigned-upload API is not complete, so the installer rejects
custom S3 topologies.

## Wildcard TLS

The bundled installer supports an operator-provided wildcard certificate. It copies the
certificate and key into `~/.orcheo/stack/app-tls/` and mounts that directory read-only
into Caddy. DNS-01 requires a custom Caddy build and provider integration managed outside
the bundled installer. Do not issue a certificate per alias. Verify DNS, certificate
chain, exact-host routing, and trusted IP derivation before enabling the feature.

For a Cloudflare-proxied origin, download the files from **Cloudflare Dashboard →
your zone → SSL/TLS → Origin Server → Create Certificate**. Cloudflare's
[Origin CA guide](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/)
describes the same flow:

1. Add `*.example.com` for the selected base domain and optionally `example.com`.
2. Let Cloudflare generate the private key and choose PEM output.
3. Save the certificate as `~/.orcheo/tls/apps-origin.pem`.
4. Save the private key as `~/.orcheo/tls/apps-origin-key.pem`.
5. Restrict both files with `chmod 600` and use Cloudflare **Full (strict)** mode.

Cloudflare shows the generated private key only once. An Origin CA certificate
is trusted between Cloudflare and the origin; direct browser connections do not
trust it. Existing paths or explicit `--app-tls-cert-file` and
`--app-tls-key-file` values override the defaults.

Missing default certificate/key files are rejected for a non-local backend. The
installer does not generate a self-signed public wildcard certificate because
browsers would not trust it. Local installs use Caddy's internal CA
automatically. Future automatic public issuance should use ACME DNS-01 with
explicit DNS-provider credentials and domain control.

## Emergency response

Platform moderation scopes are separate from workspace roles:

- `platform:hosted-apps:moderate` blocks/reinstates alias, app, workspace, or publisher.
- `platform:hosted-apps:runtime-control` changes the global runtime and requires stronger
  operator authentication.

Every action includes an idempotency key, reason code, restricted detail, and atomic
platform audit/outbox record. Workspace owners cannot lift platform blocks.

For active exploitation, disable the runtime. The transaction increments durable
generation, revokes codes/login transactions/sessions, publishes descriptor invalidation,
and blocks control, delivery, login, run, and data authorization. Redis failure must fail
closed for authorization and cost-bearing anonymous traffic.

## Retention, reconciliation, and backup

Reconcile expired staged uploads and quota reservations, unreachable partial prefixes,
expired codes/sessions/run handles/idempotency rows, delivered outbox records, collection
tombstones/records, and workspace-purge cleanup. Never prune an active release. Retain
prior releases for at least the configured rollback window and respect legal holds.

Back up PostgreSQL metadata, app records, and bundle-object rows consistently. Restore
drills verify active release references, manifests and assets, runtime generation,
session revocation, quota counters, and cleanup outbox replay before reopening traffic.

## Upgrade and rollback

Apply additive schema before starting a newer gateway/worker. Deploy the separately
subscribed validator and gateway, verify readiness, then enable allowlisted workspaces.
Rollback by disabling Hosted Apps and removing wildcard ingress; retain database and
objects for recovery. Enabling again never republishes, restores, or lifts blocks.

## Required alerts

Alert on gateway error rate, unknown/suspended host spikes, storage health, auth replay or
failure anomalies, invocation spikes, quota rejection, validation failures, outbox lag,
cache invalidation lag, and runtime-generation propagation. Logs use opaque app/workspace
ids and never include bundle contents, documents, codes, session secrets, workflow
input/output, traces, or credentials.
