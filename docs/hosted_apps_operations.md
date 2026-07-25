# Hosted Apps operator runbook

Hosted Apps is enabled by default for local installs. Public installs explicitly prompt
the operator to opt in. Apps serve traffic only when the workspace is allowlisted when
an allowlist is configured and all required domain, storage, gateway, TLS, proxy, and
runtime-generation settings validate. Setting `ORCHEO_HOSTED_APPS_ENABLED=false`
retains metadata and objects while disabling access.

`orcheo install` runs the Hosted Apps preflight before starting Compose. It validates
the base domain and wildcard DNS probe, TLS certificate paths, dedicated gateway secret,
trusted proxy CIDRs/hops, Postgres runtime state, validation queue, and bundle-storage
configuration. Run `uv run orcheo-hosted-apps-preflight` for later manual checks.

## Guided production install

With public ingress selected, interactive `orcheo install` asks whether to enable Hosted
Apps. When enabled, it prompts for:

- the bare wildcard base domain, such as `apps.example.com`;
- an optional comma-separated workspace rollout allowlist;
- trusted proxy CIDRs and a fixed forwarding-hop count;
- a readable wildcard certificate and private-key file.

The installer generates the gateway identity, copies the certificate and key into the
managed stack with restricted permissions, writes the Caddy TLS directive, validates
wildcard DNS, and only then starts Compose.

For non-interactive provisioning, provide the production values explicitly:

```bash
orcheo install --yes --public-ingress \
  --public-host orcheo.example.com \
  --hosted-apps \
  --apps-base-domain apps.example.com \
  --app-tls-cert-file /secure/wildcard-apps.pem \
  --app-tls-key-file /secure/wildcard-apps-key.pem \
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

Configure trusted proxy CIDRs/hops explicitly. The gateway strips browser Authorization,
workspace, workflow, actor, forwarding, and internal-service headers. Backend internal
routes accept only the dedicated gateway identity and are excluded from public OpenAPI.

Normal Compose startup includes the gateway, cleanup process, and a dedicated
`hosted-app-validation` Celery consumer with concurrency one. The global feature flag
still fails closed until configuration passes. Validation must never share the
workflow-worker queue. Monitor queue lag, object-store latency, and orphan cleanup.

## Storage

The bundled installer currently supports the filesystem backend for local or explicit
single-node installs. It persists bundles in the `orcheo_app_bundles` named volume.

The S3 store primitives exist, but the production presigned-upload API is not complete.
The installer therefore rejects an existing S3 Hosted Apps topology instead of accepting
credentials for a deployment-upload path that cannot work. MinIO is not bundled.

## Wildcard TLS

The bundled installer supports an operator-provided wildcard certificate. It copies the
certificate and key into `~/.orcheo/stack/app-tls/` and mounts that directory read-only
into Caddy. DNS-01 requires a custom Caddy build and provider integration managed outside
the bundled installer. Do not issue a certificate per alias. Verify DNS, certificate
chain, exact-host routing, and trusted IP derivation before enabling the feature.

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

Back up Postgres metadata and app records consistently with immutable object bytes.
Restore drills verify active release references, manifests and assets, runtime generation,
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
