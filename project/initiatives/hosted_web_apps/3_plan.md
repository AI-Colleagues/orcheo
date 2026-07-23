# Project Plan

## For Orcheo Hosted Apps

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-07-23
- **Status:** Draft

---

## Overview

Implement Orcheo Hosted Apps as a first-class workspace resource with a dedicated app-delivery gateway. The project covers app lifecycle and Studio UX, immutable prebuilt bundle storage, wildcard routing, centralized app login, app-scoped workflow bindings, managed app data, quotas, audit, abuse controls, and an optional self-hosted deployment profile.

The plan intentionally excludes server-side builds, SSR, user containers, raw database access, custom domains, and arbitrary Orcheo API proxying.

**Related Documents:**
- Requirements: [1_requirements.md](1_requirements.md)
- Design: [2_design.md](2_design.md)
- First-party Auth: [../first_party_auth/2_design.md](../first_party_auth/2_design.md)
- Multi-workspace: [../multi_workspace/2_design.md](../multi_workspace/2_design.md)
- Caddy Ingress: [../caddy_self_hosted_ingress/2_design.md](../caddy_self_hosted_ingress/2_design.md)
- ChatKit Attachment Blobs: [../chatkit_attachment_blobs/2_design.md](../chatkit_attachment_blobs/2_design.md)

---

## Milestones

### Milestone 0: Security, audience, and operational foundations

**Description:** Resolve trust-boundary and operating decisions that constrain every
implementation milestone before schema, ingress, or authentication work starts.

#### Task Checklist

- [ ] Task 0.1: Complete the baseline threat model covering publisher code, service
  workers, sibling subdomains, cookie tossing, CSRF, authorization-code interception,
  trusted proxies, gateway compromise, workflow credential/cost abuse, ZIP processing,
  object-store isolation, app-data scope, and alias reuse
  - Dependencies: Requirements and design approval
- [ ] Task 0.2: Approve the P0 audience contract: `authenticated` means a current member
  of the publisher workspace; external-customer identity remains P1
  - Dependencies: Task 0.1
- [ ] Task 0.3: Choose and document the staging, hosted-beta, and preferred production
  registrable-domain boundaries plus wildcard DNS/TLS ownership
  - Dependencies: Task 0.1
- [ ] Task 0.4: Define platform-operator hosted-app moderation scopes, separation from
  workspace roles, stronger-auth expectations, reason visibility, and reinstatement
  authority
  - Dependencies: Task 0.1
- [ ] Task 0.5: Assign publisher-verification, abuse-report, takedown, legal-hold, and
  incident-response owners and response targets
  - Dependencies: None
- [ ] Task 0.6: Specify the cross-plane global kill switch/runtime generation, including
  gateway/backend checks, cache invalidation, code/session revocation, and
  Redis-unavailable behavior
  - Dependencies: Tasks 0.1, 0.4
- [ ] Task 0.7: Define the local development contract for `*.apps.localhost`, local HTTPS,
  trusted development certificates, wildcard routing, and auth-disabled versus real-auth
  test modes
  - Dependencies: Task 0.3
- [ ] Task 0.8: Populate the execution-control table with a named DRI, contributing team,
  estimate, target date, and external dependency for every milestone
  - Dependencies: Tasks 0.3–0.7

**Exit criteria:** Threat model and domain/audience decisions are approved; platform
moderation and abuse ownership are assigned; the kill-switch and local-development
contracts are testable; no milestone remains unowned or unestimated.

---

### Milestone 1: Domain model, schema, and feature boundary

**Description:** Establish additive hosted-app domain types, storage protocols, persistence, governance settings, and feature flags without exposing public traffic.

#### Task Checklist

- [ ] Task 1.1: Add hosted-app domain models and validation under `src/orcheo/hosted_apps/`
  - Include orthogonal app state, alias lifecycle, staged uploads, deployments, immutable
    releases/capability snapshots, draft bindings, stable collections, sessions,
    authorization codes, moderation blocks, runtime-run handles, idempotency, quota
    leases, and dispatch/cleanup outboxes.
  - Dependencies: Milestone 0 and requirements/design approval
- [ ] Task 1.2: Implement alias normalization, reserved-name policy, tombstones, and stable error types
  - Dependencies: Task 1.1
- [ ] Task 1.3: Add Postgres schema and indexes for apps, aliases, uploads, deployments,
  releases, bindings, collections, records, authorization/login state, sessions,
  moderation blocks/platform audit, runtime-run mappings, idempotency, quota leases, and
  outboxes
  - Add composite `(workspace_id, app_id)` foreign keys, stable collection identifiers,
    ready-deployment/release ownership checks, and workspace-purge cleanup integration.
  - Dependencies: Task 1.1
- [ ] Task 1.4: Define repository protocols and Postgres implementations with mandatory workspace-scoped queries
  - Dependencies: Task 1.3
- [ ] Task 1.5: Add hosted-app governance settings and an operation-by-operation quota
  matrix with distributed atomic reserve/commit/release, expiry/reconciliation,
  retryability, and fail-open/fail-closed policy
  - Dependencies: Task 1.1
- [ ] Task 1.6: Add `ORCHEO_HOSTED_APPS_ENABLED`, workspace allowlist, durable runtime
  generation, and configuration validation that fails closed when required
  domain/storage/runtime values are absent
  - Implement the Milestone 0 global-disable path across control plane, gateway contract,
    login state, codes, sessions, descriptors, and runtime authorization.
  - Dependencies: Tasks 0.6, 1.3
- [ ] Task 1.7: Add an endpoint-to-role-to-audit-action matrix for every control-plane and
  platform moderation mutation
  - Persist sensitive mutation and audit/outbox atomically; never swallow an audit
    failure after committing publication, permission, alias, or suspension state.
  - Dependencies: Task 1.4
- [ ] Task 1.8: Add unit and repository tests for normalization, conflicts, lifecycle
  overlays/transition table, immutable release snapshots, draft/live separation,
  composite tenant constraints, stable collection identity, idempotent schema
  initialization, audit fault injection, kill-switch generation, and cross-workspace denial
  - Dependencies: Tasks 1.2–1.7

**Exit criteria:** New schema is additive and idempotent; app state, tenant ownership,
alias ownership, immutable release publication, audits, and cleanup outboxes are
transactionally safe; allowlist/global-disable enforcement is proven while the feature
remains externally disabled.

---

### Milestone 2: Control-plane API and Studio authoring

**Description:** Let workspace users create and manage draft apps, permissions, and deployment metadata from a dedicated Studio area.

#### Task Checklist

- [ ] Task 2.1: Add protected `/api/apps` routers and response/request schemas
  - Dependencies: Milestone 1
- [ ] Task 2.2: Enforce workspace roles: viewer read, editor draft/upload, admin/owner alias/grant/publish/archive operations
  - Dependencies: Task 2.1
- [ ] Task 2.3: Add atomic editor-authorized app creation/initial alias reservation plus
  list/detail/update/archive/restore endpoints with cursor pagination and every audit actor
  derived from authentication
  - Dependencies: Tasks 2.1, 2.2
- [ ] Task 2.4: Add draft binding CRUD with same-workspace workflow validation, immutable
  graph checksum plus server-copied runnable-config snapshots/executable digests, JSON
  Schema subset validation, documented
  output projection/size, output/error readability flags, and per-IP/session/app,
  concurrency, and timeout limit validation
  - Harden the existing workflow runnable-config mutation to derive actor from
    authentication, enforce its workspace role, and emit dependency invalidation; never
    trust the request-body actor for hosted-app review state.
  - Dependencies: Tasks 2.1, 1.4
- [ ] Task 2.5: Add stable-id draft collection-definition CRUD with shared/user scope,
  explicit anonymous/authenticated read/write access, quotas, tombstoning, and safe
  delete/recreate-name semantics
  - Dependencies: Tasks 2.1, 1.4
- [ ] Task 2.6: Add draft capability revisions and publish-review calculation; prove draft
  expansion cannot affect the active release and emergency reductions can fail closed
  - Dependencies: Tasks 2.4, 2.5
- [ ] Task 2.7: Add `apps` feature navigation, route, API client, and workspace-aware query keys in Studio
  - Dependencies: Task 2.1
- [ ] Task 2.8: Build Studio apps list, create dialog, app detail, draft-versus-live access
  display, executable digests, bindings, collections, capability diff, and audit/status
  summaries
  - Dependencies: Tasks 2.3–2.7
- [ ] Task 2.9: Add backend and Studio tests for role boundaries, workspace switching, error states, alias conflicts, and stale permission review
  - Dependencies: Tasks 2.3–2.8

**Exit criteria:** Selected workspaces can manage complete draft app metadata and runtime
grants without uploading or serving content; initial alias creation and every mutation
have an explicit role and durable audit event; live release authority remains unchanged by
draft edits.

---

### Milestone 3: Bundle upload, validation, and immutable deployments

**Description:** Accept prebuilt ZIP archives, validate them without execution, and persist immutable extracted deployments in a provider-neutral bundle store.

#### Task Checklist

- [ ] Task 3.1: Define `AppBundleStore` protocol for staged upload, immutable writes/reads, manifest storage, prefix deletion, and health checks
  - Dependencies: Milestone 1
- [ ] Task 3.2: Implement S3-compatible bundle storage with private buckets/objects and server-only credentials
  - Dependencies: Task 3.1
- [ ] Task 3.3: Implement a filesystem bundle store restricted to local development/single-node configuration
  - Dependencies: Task 3.1
- [ ] Task 3.4: Add upload-init and completion contracts, including presigned production uploads and bounded local multipart fallback
  - Persist expiring one-time upload records, reserve quota before signing, constrain
    provider-supported size/checksum metadata, verify authoritative object metadata on
    completion, and reconcile abandoned reservations/objects.
  - Dependencies: Tasks 2.1, 3.1
- [ ] Task 3.5: Add task routing, a dedicated Celery deployment-validation queue, a
  separately deployed validation-worker consumer, and idempotent validation/outbox dispatch
  - Bound CPU/memory/concurrency, add queue-lag health, and wire local, staging, hosted,
    and self-hosted Compose profiles so a named queue is always consumed without using the
    workflow-worker pool.
  - Dependencies: Tasks 3.1, 3.4
- [ ] Task 3.6: Implement streaming ZIP validation for compressed/expanded size, file
  count, per-file size, path depth, malformed filename encoding, absolute/parent
  traversal, symlinks, hard links, special files, executable formats, duplicates,
  Unicode/case-fold collisions, nested archives, reserved `__orcheo/`, and root
  `index.html`
  - Dependencies: Task 3.5
- [ ] Task 3.7: Generate a server-authoritative asset manifest with normalized paths,
  derived MIME types, sizes, digests, and per-HTML policy containing SHA-256 hashes of
  supported inline scripts; reject inline event handlers, `javascript:` URLs, and other
  unsupported executable constructs
  - Dependencies: Task 3.6
- [ ] Task 3.8: Implement logical object-store atomicity without prefix rename: write
  idempotently to a unique final prefix, write/verify the manifest last, then
  transactionally mark ready; reconcile partial/expired uploads and unreachable prefixes
  - Dependencies: Tasks 3.2–3.7
- [ ] Task 3.9: Add deployment list/detail/status APIs and Studio upload/history/validation-error UI
  - Dependencies: Tasks 3.4–3.8
- [ ] Task 3.10: Add unit, property, fuzz, MinIO integration, provider-condition,
  completion-replay, crash-at-every-phase, cleanup, retry, inline-script CSP, reserved
  namespace, executable-format, and zip-bomb regression tests
  - Dependencies: Tasks 3.2–3.8

**Exit criteria:** Valid prebuilt bundles become immutable ready deployments with
deployment-specific CSP metadata; malformed or dangerous archives cannot become
publishable; no authoritative partial deployment or leaked quota reservation remains; the
validation workload is isolated from workflow execution.

---

### Milestone 4: App gateway and public static delivery

**Description:** Add the isolated app-delivery data plane, wildcard routing, host resolution, cache invalidation, static serving, and public app lifecycle.

#### Task Checklist

- [ ] Task 4.1: Scaffold `apps/app_gateway/` as a separately runnable ASGI service with health/readiness endpoints
  - Add the package to the uv workspace/lock, define its entry point, Docker/image and
    release strategy, CI/type/lint/test jobs, SBOM/vulnerability scan, and local/staging/
    hosted/self-hosted Compose wiring.
  - Dependencies: Milestones 1, 3
- [ ] Task 4.2: Add dedicated gateway internal service identity/scopes, explicit backend
  internal host/runtime routes, and host-resolution endpoint
  - Mount outside the client-selected workspace lane, set `include_in_schema=False`,
    reserve `/internal/` from Studio SPA fallback, and reject user JWTs, ordinary service
    tokens, spoofed internal headers, and gateway access to general APIs.
  - Dependencies: Tasks 1.4, 4.1
- [ ] Task 4.3: Implement exact wildcard-host validation, canonical alias/release
  resolution, and trusted-proxy client-IP derivation from configured hops/CIDRs
  - Dependencies: Task 4.2
- [ ] Task 4.4: Implement active-release descriptor cache with maximum TTL, negative
  cache, app/global runtime generation, ETag, and Redis invalidation/fallback
  - Dependencies: Tasks 4.2, 4.3
- [ ] Task 4.5: Implement manifest-only asset lookup and streaming object-store responses
  - Dependencies: Tasks 3.7, 4.3
- [ ] Task 4.6: Implement SPA fallback while reserving `/__orcheo/` and rejecting ambiguous/unsafe URL paths
  - Dependencies: Task 4.5
- [ ] Task 4.7: Add release-specific CSP with manifest inline-script hashes and
  non-relaxable `worker-src 'none'`, mandatory response headers, same-origin CORP,
  server-derived content types, and safe cache policies
  - Original alias paths and HTML revalidate; private assets/runtime responses are
    `private, no-store`; only platform-verifiable deployment/content-addressed URLs may
    use long-lived immutable caching.
  - Dependencies: Tasks 4.5, 4.6
- [ ] Task 4.8: Add transactional immutable-release publish, unpublish, rollback, and
  workspace-admin suspension endpoints with app-row CAS/locking, dependency/executable
  digest validation, atomic audit/outbox, and cache invalidation
  - Dependencies: Tasks 2.6, 4.4
- [ ] Task 4.9: Add Studio deployment publish review, publish, rollback, unpublish, and canonical URL controls
  - Dependencies: Task 4.8
- [ ] Task 4.10: Add wildcard app-domain routing and app-gateway service to staging stack assets
  - Dependencies: Tasks 4.1, 4.3
- [ ] Task 4.11: Provision staging wildcard DNS and TLS; prefer one wildcard certificate rather than per-alias issuance
  - Dependencies: Task 4.10
- [ ] Task 4.12: Add Compose and real-browser tests for host/proxy routing, local HTTPS,
  service-worker registration denial, inline-script hashes, same-path cross-deployment
  caching, private no-store behavior, SPA paths, internal-route isolation, concurrent
  publish, rollback, unpublish, workspace suspension, platform blocks, and global disable
  - Dependencies: Tasks 4.4–4.11

**Exit criteria:** Staff can publish and roll back immutable public releases on a staging
wildcard domain without routing asset traffic through the primary backend process;
publisher code cannot register a service worker or shadow reserved paths; same-path
republish loads correct bytes; platform/global revocation meets the SLO.

---

### Milestone 5: App-scoped workflow runtime

**Description:** Let browser applications invoke only explicitly configured workflow bindings and observe sanitized, app-scoped results.

#### Task Checklist

- [ ] Task 5.1: Add internal app-runtime router protected exclusively by gateway service identity
  - Dependencies: Tasks 4.2, 2.4
- [ ] Task 5.2: Strip and reject browser attempts to supply internal service headers,
  general bearer tokens, workspace headers, actor ids, workflow ids, forwarding headers,
  or trusted-client-IP assertions; require exact Origin, strict JSON media type, and Fetch
  Metadata on every state-changing route before anonymous invocation is enabled
  - Dependencies: Task 5.1
- [ ] Task 5.3: Implement server-side resolution of host -> active immutable release ->
  deployment -> logical binding snapshot -> verified graph checksum and copied
  runnable-config executable digest
  - Update the app-originated worker path to require the release snapshot explicitly and
    never fall back to the workflow version's mutable current runnable configuration.
  - Dependencies: Tasks 4.3, 5.1
- [ ] Task 5.4: Implement binding input byte/JSON Schema validation and anonymous/authenticated access checks
  - Dependencies: Task 5.3
- [ ] Task 5.5: Add app-specific per-IP, per-session, per-binding, per-app, and
  workspace-governance rate/concurrency checks using distributed atomic leases
  - Consume only the gateway-authenticated normalized client IP; define reservation TTL,
    release, crash reconciliation, retry semantics, and fail-closed behavior for anonymous
    cost-bearing traffic when governance is unavailable.
  - Dependencies: Tasks 5.3, 1.5
- [ ] Task 5.6: Implement durable app-originated workflow acceptance with internal run,
  public handle, quota lease, scoped request-hash/idempotency record, and dispatch outbox
  in one transaction
  - Dispatch after commit, retry broker failures, and atomically claim pending work before
    execution through the existing worker path with trusted release metadata and workspace
    accounting.
  - Dependencies: Tasks 5.3–5.5
- [ ] Task 5.7: Create opaque app-run mappings and status lookup that never exposes
  internal workflow-run identifiers
  - Bind authenticated results to the originating current session/user; define anonymous
    handles as high-entropy, short-lived bearer capabilities; enforce result expiry and
    runtime-generation revocation.
  - Dependencies: Task 5.6
- [ ] Task 5.8: Implement documented output projection, maximum encoded output bytes,
  explicit visitor output/error flags, sanitized error mapping, cooperative cancellation,
  and a hard worker timeout; default to no output fields unless explicitly configured
  - Dependencies: Task 5.7
- [ ] Task 5.9: Add gateway browser routes for `POST /__orcheo/workflows/{binding}/runs`
  and `GET /__orcheo/runs/{handle}` with required `Idempotency-Key`, exact-Origin
  enforcement, `private, no-store`, and same-origin CORP
  - Dependencies: Tasks 5.1–5.8
- [ ] Task 5.10: Add integration and security tests for cross-app/workspace/binding
  denial, changed executable digests/archived parent workflows, spoofed IP, Origin bypass,
  idempotent retry, broker outage, atomic claim, quota lease races/crash recovery,
  timeout, result ownership, output/error leakage, and immediate revocation
  - Dependencies: Tasks 5.4–5.9

**Exit criteria:** A public app can durably and idempotently invoke an explicitly anonymous
release binding under distributed hard limits, while no app can access arbitrary
workflows, mutable unapproved execution state, vault APIs, general run history, traces, or
unrelated results.

---

### Milestone 6: Central authentication and private apps

**Description:** Extend first-party identity with a secure app authorization-code exchange and host-scoped app sessions without exposing Studio tokens to uploaded JavaScript.

#### Task Checklist

- [ ] Task 6.1: Add hashed, short-lived, single-use app authorization-code repository methods and atomic consume semantics
  - Dependencies: Milestone 1
- [ ] Task 6.2: Add the protected Studio authorization endpoint with the explicit
  `app_id`, canonical `app_host`, state, and PKCE S256 contract
  - Derive workspace/callback from the resolved app rather than Studio's selected
    workspace; accept no caller-selected redirect URI; require current publisher-workspace
    membership and active release.
  - Dependencies: Tasks 6.1, 2.3
- [ ] Task 6.3: Add Studio app-authorization route that parses the gateway redirect
  contract, uses the existing first-party login/session, displays app/publisher identity,
  calls the protected endpoint, and returns only to the backend-derived callback
  - Dependencies: Task 6.2
- [ ] Task 6.4: Implement server-side gateway login transactions containing PKCE verifier,
  state hash, canonical host, safe relative return path, and expiry
  - Store only a separate opaque secret in a host-only `__Host-orcheo_app_login`,
    HttpOnly, Secure, SameSite=Lax cookie; app code cannot read state or verifier.
  - Dependencies: Milestone 4
- [ ] Task 6.5: Add internal authorization-code exchange with PKCE verification, atomic code consumption, and membership recheck
  - Dependencies: Tasks 6.1, 6.4
- [ ] Task 6.6: Add hashed app sessions, current-membership introspection, configurable
  12-hour absolute and 30-minute idle defaults, runtime-generation checks, revocation, and
  coarse last-seen updates; bind each session to the exact canonical app host so alias
  changes cannot transfer it
  - Dependencies: Task 6.5
- [ ] Task 6.7: Set only `__Host-` prefixed, host-only, HttpOnly, Secure app cookies and ensure no parent-domain auth cookie is introduced
  - Dependencies: Task 6.6
- [ ] Task 6.8: Add membership/app/workspace/moderation/global-disable lifecycle hooks that
  revoke login transactions, authorization codes, and app sessions
  - Introspection still rechecks authoritative membership/status so safety does not depend
    on best-effort hooks.
  - Dependencies: Task 6.6
- [ ] Task 6.9: Enforce private app sessions before bundle delivery and authenticated binding/data access
  - Dependencies: Tasks 6.6, 4.5, 5.4
- [ ] Task 6.10: Add `/__orcheo/config`,
  `GET /__orcheo/auth/start?return_to=<safe-relative-path>`, callback, session, and
  `POST /__orcheo/auth/logout` browser routes for private and optional public-app login
  - Dependencies: Tasks 6.3–6.9
- [ ] Task 6.11: Add session-bound CSRF protection on top of the exact-Origin, strict
  media-type, and Fetch Metadata foundation already required by Milestone 5
  - Dependencies: Tasks 6.7, 6.10
- [ ] Task 6.12: Add transaction-cookie theft, publisher-script callback interception,
  replay, callback/app-host confusion, selected-workspace confusion, open redirect,
  cross-alias cookie, removed-membership, absolute/idle expiry, optional public login,
  CSRF, moderation/global-disable, and token-leak regression tests
  - Dependencies: Tasks 6.2–6.11

**Exit criteria:** Private apps and optional authenticated runtime features reuse Orcheo
login for current publisher-workspace members only; the full redirect contract is
implemented; publisher code and browser-readable storage never contain Studio tokens,
authorization codes, PKCE verifiers, or app-session secrets.

---

### Milestone 7: Managed app-data API

**Description:** Provide bounded JSON document persistence scoped by workspace, app, declared collection, and optional authenticated visitor.

#### Task Checklist

- [ ] Task 7.1: Implement stable collection identifiers, collection-name validation,
  shared/user scoping, explicit read/write authorization, tombstoning, and safe
  delete/recreate-name behavior
  - Dependencies: Tasks 2.5, 6.6
- [ ] Task 7.2: Implement scoped app-record repository methods with mandatory
  workspace/app/stable-collection/owner predicates and composite database foreign keys
  - Dependencies: Tasks 1.3, 7.1
- [ ] Task 7.3: Add create/get/update/list/delete service with canonical JSON size, depth,
  key, row, byte, and rate limits plus distributed quota reservations for writes
  - Dependencies: Tasks 7.1, 7.2
- [ ] Task 7.4: Add optimistic concurrency versions and opaque cursor pagination
  - Dependencies: Task 7.3
- [ ] Task 7.5: Add exact-Origin/CSRF-protected browser runtime routes under
  `/__orcheo/data/{collection}` with same-origin CORP and `private, no-store`
  - Dependencies: Tasks 5.1, 7.3, 7.4
- [ ] Task 7.6: Add reconciled usage accounting and admin-visible collection row/byte
  summaries without exposing visitor documents; record Studio-side inspection/export as
  an explicit P1 follow-up
  - Dependencies: Tasks 7.2, 7.3
- [ ] Task 7.7: Add cross-workspace/app/user tests, client-supplied scope override,
  collection-name reuse/no-resurrection, delete-retention, membership-removal,
  distributed quota race/crash, stale update, pagination, cache-header, and log-redaction
  tests
  - Dependencies: Tasks 7.2–7.6

**Exit criteria:** Apps can persist declared shared or workspace-member-private documents
without obtaining database credentials or querying internal Orcheo tables; deleted
collection names cannot resurrect data; quotas and retention remain correct under race,
retry, and workspace lifecycle changes.

---

### Milestone 8: Security hardening, abuse operations, and observability

**Description:** Complete the release-blocking controls needed before third-party public publishing.

#### Task Checklist

- [ ] Task 8.1: Revisit and approve the Milestone 0 threat model against implemented
  controls, including service-worker denial, release snapshots, trusted proxy/IP,
  idempotent dispatch, global disable, and deletion/reconciliation evidence
  - Dependencies: Milestones 3–7
- [ ] Task 8.2: Revalidate the approved registrable-domain decision and sibling-domain
  release blockers against browser tests and final ingress configuration
  - Dependencies: Tasks 0.3, 8.1
- [ ] Task 8.3: Add platform-scoped operator APIs/CLI for reserved aliases,
  app/alias/workspace/publisher blocks, reason capture, reinstatement, and ownership lookup
  - Mount outside selected-workspace authority; require explicit global moderation scopes;
    persist moderation/audit atomically; prove workspace owners/admins are denied.
  - Dependencies: Tasks 1.4, 4.8
- [ ] Task 8.4: Add generic suspended/unavailable interstitial and abuse-report link without embedding trusted login UI in app content
  - Dependencies: Task 8.3
- [ ] Task 8.5: Exercise the assigned publisher-verification, abuse-report/takedown,
  legal-hold, escalation, and response-target process from intake through reinstatement
  - Dependencies: Task 0.5
- [ ] Task 8.6: Add metrics and safe structured logs for control lifecycle, validation, gateway delivery, auth, sessions, runtime runs, data usage, quotas, and cache invalidation
  - Dependencies: Milestones 3–7
- [ ] Task 8.7: Add operator dashboards/alerts for gateway error rate,
  unknown/suspended hosts, object-store health, auth anomalies, invocation spikes, quota
  rejection, and validation failures; add workspace-safe per-app aggregate health API and
  Studio views without visitor documents, workflow input/output, or trace leakage
  - Dependencies: Task 8.6
- [ ] Task 8.8: Add idempotent retention/pruning/reconciliation jobs for upload
  reservations, staged/partial prefixes, codes/login transactions/sessions/run handles,
  idempotency/outbox rows, prior releases/deployments, collection tombstones/records,
  workspace purge, backup aging, quota counters, legal holds, and operator evidence
  - Dependencies: Milestones 3, 6, 7
- [ ] Task 8.9: Run static analysis, dependency review, archive fuzzing, browser security-header review, and targeted penetration testing
  - Dependencies: Tasks 8.1–8.8
- [ ] Task 8.10: Validate workspace suspension, every platform block scope, and
  global-disable propagation under warm caches and Redis outage against the release SLO
  - Dependencies: Tasks 4.4, 8.3
- [ ] Task 8.11: Run a restore drill for consistent Postgres metadata, release manifests,
  object bytes, sessions/revocation generation, quotas, and cleanup outbox reconciliation
  - Dependencies: Tasks 8.6–8.8

**Exit criteria:** Security re-review is approved; verified-publisher/takedown operations
have been exercised; monitoring detects abuse and reliability regressions; every
moderation/global-disable scope meets the propagation SLO; restore and cleanup
reconciliation complete without cross-scope or active-release loss.

---

### Milestone 9: Hosted beta rollout

**Description:** Dogfood the full feature, then expand gradually to selected verified workspaces under conservative limits.

#### Task Checklist

- [ ] Task 9.1: Verify the Milestone 1 feature flag, workspace allowlist, runtime
  generation, and kill-switch behavior in the hosted environment before adding staff ids
  - Dependencies: Milestones 1, 4, 8
- [ ] Task 9.2: Publish representative internal plain HTML and Vite apps using public/private access, workflow bindings, and app data
  - Dependencies: Milestones 4–7
- [ ] Task 9.3: Run the documented beta load profile and prove app traffic increases
  Studio/API p95 latency by no more than 5% and error rate by no more than 0.1 percentage
  points
  - Dependencies: Milestones 4, 8
- [ ] Task 9.4: Run workflow invocation load and quota-race tests with anonymous and authenticated bindings
  - Dependencies: Milestones 5, 8
- [ ] Task 9.5: Complete end-to-end browser matrix for desktop/mobile login, optional
  public-app login, callback, private assets, inline-script CSP, service-worker denial,
  same-path republish/rollback, app data, workspace suspension, platform blocks, and
  global disable
  - Dependencies: Milestones 4–8
- [ ] Task 9.6: Document author bundle format, inline-script hashing and unsupported
  constructs, service-worker prohibition, runtime/idempotency API, publisher-workspace-only
  authenticated audience, bindings, data collections, CSP, quotas, local HTTPS, and
  troubleshooting
  - Dependencies: Milestones 3–7
- [ ] Task 9.7: Document hosted operator runbooks for storage, DNS/TLS/trusted proxies,
  validation workers, cache/runtime-generation invalidation, moderation scopes, global
  disable, secret rotation, retention/reconciliation, backup, restore, and incident response
  - Dependencies: Milestone 8
- [ ] Task 9.8: Enable selected verified workspaces and evaluate a versioned go/no-go
  scorecard at each expansion
  - Define exact telemetry queries, eligible denominators, rolling seven-day windows,
    minimum sample sizes, DRI, thresholds, rollback triggers, and evidence links for every
    requirements KPI/guardrail.
  - Dependencies: Tasks 9.1–9.7
- [ ] Task 9.9: Decide whether hosted beta is ready for broader public-app creation
  - Dependencies: Task 9.8 and all launch gates

**Exit criteria:** Selected workspaces meet the requirements' precisely queried publish,
time-to-first-app, delivery, isolation, rollback, abuse-response, and core-API guardrails
over the approved observation window; restore/global-disable drills pass; no
release-blocking security or operational finding remains.

---

### Milestone 10: Optional self-hosted stack profile

**Description:** Package Hosted Apps for operators who explicitly configure wildcard networking and compatible bundle storage.

#### Task Checklist

- [ ] Task 10.1: Add app-gateway, separately subscribed validation-worker, and hosted-app
  cleanup/reconciliation services to the `deploy/stack/docker-compose.yml`
  `hosted-apps` profile
  - Dependencies: Hosted beta stack stabilization
- [ ] Task 10.2: Extend Caddy configuration for `*.<ORCHEO_APPS_BASE_DOMAIN>` and document that one wildcard matches exactly one alias label
  - Dependencies: Task 10.1
- [ ] Task 10.3: Provide wildcard TLS options: operator-provided certificate or DNS-01-capable Caddy build/provider
  - Dependencies: Task 10.2
- [ ] Task 10.4: Add setup validation for apps base domain, wildcard DNS, TLS method,
  trusted proxy CIDRs/hops, app gateway secret/scope, runtime generation store,
  validation-worker subscription, and S3-compatible storage
  - Dependencies: Tasks 10.1–10.3
- [ ] Task 10.5: Decide whether MinIO is bundled or externally configured; keep the default profile from silently creating an unsupported production storage topology
  - Dependencies: Task 10.4
- [ ] Task 10.6: Update stack env template, manual setup, deployment, environment-variable, backup, and upgrade documentation
  - Dependencies: Tasks 10.1–10.5
- [ ] Task 10.7: Add Compose smoke tests for Studio/API/internal host isolation, wildcard
  app host, trusted IP derivation, bundle upload/isolated validation/serve, private login,
  runtime API/outbox, moderation/global disable, cleanup reconciliation, and restart
  persistence
  - Dependencies: Tasks 10.1–10.6
- [ ] Task 10.8: Validate on a reachable self-hosted Linux host with real wildcard DNS and HTTPS
  - Dependencies: Task 10.7

**Exit criteria:** Self-hosted operators can opt in without changing existing local/public ingress behavior, and documentation clearly states wildcard DNS/TLS, storage, abuse, and scale responsibilities.

---

## Cross-cutting Validation Gates

### Code quality

- Python: `make format`, `make lint`, and targeted/full `make test` as risk requires.
- Studio: `make studio-format`, `make studio-lint`, and `make studio-test`.
- Gateway: format, type checking, unit tests, dependency audit, container health checks, and image vulnerability scan.
- Gateway and validation worker are included in the uv workspace/lock, image/release
  pipeline, SBOM, Compose profiles, and CI before their milestones can exit.
- Generated OpenAPI/client types remain synchronized where used.

### Security release gates

- No Studio access token, refresh token, app authorization code, app-session secret, internal service token, or vault credential appears in bundle responses, browser-readable storage, runtime config, logs, or error payloads.
- Cross-workspace, cross-app, cross-user, cross-alias, and arbitrary-workflow tests pass.
- Parent-domain authentication cookies are absent.
- Origin/CSRF, PKCE, exact callback, state, code replay, and open-redirect tests pass.
- Publisher code cannot register a service worker, shadow `/__orcheo/`, intercept auth
  callbacks, or retain origin control across suspension/alias reuse.
- Draft permission expansion, visibility, CSP origins, or workflow runnable-config changes
  cannot alter an active immutable release without a new admin publish.
- Same-path republish/rollback serves the correct deployment bytes; private and runtime
  responses are not stored; only verified content-addressed URLs are immutable.
- App runtime cannot reach general Orcheo API routes using gateway service identity.
- Operator suspension and global disable propagate within the documented maximum.
- Public publishing remains allowlisted until abuse reporting and takedown operations are staffed.

### Reliability and performance gates

- Static delivery meets beta availability and latency targets under representative bundles.
- App load does not materially regress Studio/control API latency.
- Validation jobs cannot starve workflow execution queues.
- Object-store, backend, Redis, and worker failure behavior matches the design and fails closed for authorization.
- Publish and rollback are atomic under concurrent requests.
- Run acceptance survives broker outage without duplicates or stranded handles; worker
  claiming is atomic and idempotent.
- Rate/concurrency/upload/session/data quotas remain correct under race, retry, crash,
  lease expiry, reconciliation, and multi-replica Redis failure policy.
- Restore and cleanup reconciliation prove active releases, referenced objects, tenant
  boundaries, quotas, and deletion outboxes are consistent.

### Documentation gates

- Author guide explains static-only format, `index.html`, limits, runtime paths, binding/data permissions, CSP, and errors.
- Author guide explicitly explains workspace-member-only P0 authentication, inline-script
  hashing/rejection, service-worker prohibition, idempotency keys, safe caching, and local
  HTTPS.
- Operator guide explains feature flags, domain, wildcard DNS/TLS, storage, secrets, quotas, retention, backup/restore, metrics, and suspension.
- Security guide explains untrusted publisher code, permission review, app-domain isolation, and why raw database/vault/API credentials are never exposed.

---

## Execution Controls

The plan remains `Draft`. Before any milestone starts, Task 0.8 must replace every
placeholder below. A milestone with an unassigned DRI, no estimate, or no target may not
enter `in progress`.

| Milestone | DRI | Contributing teams | Estimate | Target | External dependencies |
|-----------|-----|--------------------|----------|--------|-----------------------|
| 0 Security/audience foundations | Unassigned | Security, Product, Infra | Unestimated | Unscheduled | Domain, abuse/legal ownership |
| 1 Domain/schema | Unassigned | Core, Backend | Unestimated | Unscheduled | Milestone 0 approval |
| 2 Control plane/Studio | Unassigned | Backend, Studio | Unestimated | Unscheduled | Milestone 1 |
| 3 Bundle validation | Unassigned | Backend, Infra | Unestimated | Unscheduled | Object store, worker capacity |
| 4 Gateway/public delivery | Unassigned | Backend, Infra, Studio | Unestimated | Unscheduled | Wildcard DNS/TLS |
| 5 Workflow runtime | Unassigned | Backend, Runtime, Infra | Unestimated | Unscheduled | Distributed governance design |
| 6 Authentication/private apps | Unassigned | Identity, Backend, Studio | Unestimated | Unscheduled | Milestones 4–5 |
| 7 App data | Unassigned | Backend, Data/Infra | Unestimated | Unscheduled | Milestones 1, 5–6 |
| 8 Hardening/operations | Unassigned | Security, Infra, Backend | Unestimated | Unscheduled | Milestones 3–7 |
| 9 Hosted beta | Unassigned | Product, Infra, Security | Unestimated | Unscheduled | Milestone 8 gates |
| 10 Self-hosted profile | Unassigned | Infra, Docs | Unestimated | Unscheduled | Hosted beta stabilization |

The DRI maintains a decision log, risk register, current estimate, milestone demo, and
evidence links for exit criteria. External lead times such as certificates, security
review, penetration testing, publisher verification, and takedown staffing are scheduled
explicitly rather than hidden inside engineering estimates.

---

## Requirement-to-Test Traceability

| Requirement area | Primary tasks | Release evidence |
|------------------|---------------|------------------|
| Immutable release and permission review | 1.1–1.4, 2.4–2.6, 4.8 | Concurrent publish tests; draft expansion cannot change live release |
| Browser-origin and cache isolation | 0.1, 3.7, 4.3–4.7, 4.12 | Service-worker denial; same-path republish; private/runtime no-store |
| App authentication | 0.2, 6.1–6.12 | Full redirect/PKCE/session matrix; membership and global revocation |
| Workflow bindings and cost controls | 2.4, 5.1–5.10 | Executable-digest, idempotency/outbox, quota race, timeout, ownership tests |
| App data and tenant isolation | 1.3, 2.5, 7.1–7.7 | Composite-FK, stable-id, no-resurrection, cross-user, retention tests |
| Upload/storage safety | 3.1–3.10 | Presign constraints, completion replay, archive fuzz, crash cleanup, MinIO |
| Moderation/global disable | 0.4–0.6, 1.6–1.7, 8.3–8.10 | Role denial, every block scope, warm-cache/Redis-outage SLO drill |
| Retention/backup/restore | 1.3, 7.7, 8.8, 8.11 | Workspace purge, object reconciliation, quota settlement, restore drill |
| Rollout metrics | 8.6–8.7, 9.3–9.9 | Versioned go/no-go scorecard with queries, windows, samples, and DRI |

---

## Critical Path and Parallel Work

The actual time-based critical path cannot be calculated until Task 0.8 supplies
estimates. The dependency-critical sequence is:

1. Milestone 0 security/audience/operations decisions.
2. Milestone 1 domain/schema/release boundary.
3. Milestone 2 control-plane contracts required by upload, publish, runtime, and data.
4. Milestone 3 deployment artifacts and isolated validation.
5. Milestone 4 gateway/public serving and immutable releases.
6. Milestone 5 durable runtime authorization.
7. Milestones 6 and 7 authentication/private apps and app data, which may overlap after
   their shared contracts stabilize.
8. Milestone 8 security/operations/restore gates.
9. Milestone 9 beta rollout.

Parallelizable after Milestone 1:

- Studio draft control plane can progress alongside bundle-store implementation.
- Identity authorization-code and server-side login-transaction storage can progress
  alongside public gateway work after Milestone 0 fixes the contract.
- App-data repository work can begin after schema/collection contracts while workflow runtime is implemented.
- Documentation and operator runbook drafts can begin once each contract stabilizes.

---

## Deferred Follow-up Initiatives

- Custom domains, ownership verification, and on-demand TLS.
- Deployment preview URLs and branch environments.
- Browser SDK and runtime streaming over SSE/WebSocket.
- External-customer identity and consent for authenticated public applications beyond
  publisher-workspace membership.
- Audited workspace-admin app-data inspection/export with privacy and retention controls.
- Service workers/offline-first PWAs only under a separate origin/revocation design.
- Public Suffix List submission after the hosted service is established.
- App analytics, budgets, billing, and marketplace/discovery.
- Build-from-source.
- SSR, server functions, arbitrary containers, or other server-side user code.
- Rich relational app database schemas, SQL access, or database branching.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-07-23 | Codex | Initial draft |
| 2026-07-23 | Codex | Added pre-implementation security decisions, immutable release and executable-digest work, complete auth/runtime/upload/data lifecycle tasks, isolated validation/gateway packaging, measurable gates, execution controls, and traceability after review |
