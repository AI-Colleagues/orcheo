# Hosted Apps operator runbook

Hosted Apps is disabled unless `ORCHEO_HOSTED_APPS_ENABLED=true`, the workspace is
allowlisted when an allowlist is configured, and all required domain, storage, gateway,
and runtime-generation settings validate. Disablement retains metadata and objects.

Before enabling the profile, run `uv run orcheo-hosted-apps-preflight`. It validates
the base domain and wildcard DNS probe, TLS method, dedicated gateway secret, trusted
proxy CIDRs/hops, Postgres runtime state, validation queue, and selected bundle-storage
configuration.

## Network and service topology

The trusted Studio/API hostname continues routing to Studio/backend. Exactly one wildcard
label under `ORCHEO_APPS_BASE_DOMAIN` routes to `app-gateway:2030`. A wildcard certificate
matches `*.example.test`, not deeper names such as `a.b.example.test`.

Configure trusted proxy CIDRs/hops explicitly. The gateway strips browser Authorization,
workspace, workflow, actor, forwarding, and internal-service headers. Backend internal
routes accept only the dedicated gateway identity and are excluded from public OpenAPI.

The `hosted-apps` Compose profile starts the gateway and a dedicated
`hosted-app-validation` Celery consumer with concurrency one. Validation must never share
the workflow-worker queue. Monitor queue lag, object-store latency, and orphan cleanup.

## Storage

Production uses a private S3-compatible bucket with server-only credentials. Configure an
endpoint, region, bucket, access key, and secret through stack secret handling. Objects
are written to unique immutable deployment prefixes; assets are written idempotently,
the authoritative manifest is verified and written last, and only then is the deployment
marked ready.

The filesystem backend is accepted only for local or explicit single-node installs.
MinIO is not bundled by default: operators either provide external S3-compatible storage
or deliberately add and own a supported MinIO topology.

## Wildcard TLS

Use either an operator-provided wildcard certificate mounted into ingress or a
DNS-01-capable Caddy build/provider with narrowly scoped DNS credentials. Do not issue a
certificate per alias. Verify DNS, certificate chain, exact-host routing, and trusted IP
derivation before enabling the feature.

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
