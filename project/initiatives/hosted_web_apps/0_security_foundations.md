# Hosted Apps Security and Operating Foundations

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-07-24
- **Status:** Approved

This document turns the requirements and design into explicit release gates. Hosted Apps
remains disabled until the implementation evidence for the relevant release gate exists.

## P0 audience contract

`authenticated` means a current member of the workspace that publishes the app.
The gateway independently rechecks that membership on each app-session introspection.
External-customer identity and external-user-scoped data are P1; public visitors may
only use explicit anonymous bindings and shared anonymous collections.

## Domain and ingress boundaries

| Environment | App domain | DNS/TLS owner | Release rule |
|---|---|---|---|
| Local | `*.apps.localhost` | Developer machine; Caddy local CA | HTTPS and a trusted local certificate are required for auth acceptance tests. |
| Staging | `*.apps.staging.orcheo.cloud` | Infrastructure DRI | Wildcard DNS and one wildcard certificate route only one alias label to the gateway. |
| Hosted beta | `*.beta.orcheo.cloud` | Infrastructure DRI | Staff/allowlisted publishers only; no parent-domain cookie, and browser security tests are release blockers. |
| Preferred production | `*.<dedicated user-content registrable domain>` | Infrastructure DRI | Acquire and configure a registrable domain separate from trusted Studio/API origins before open public creation. |

The gateway accepts an exact host matching one configured base domain and exactly one
alias label. It does not infer trust from `X-Forwarded-*`; client IP is derived only
from a configured ingress hop/CIDR chain. Caddy/ingress owns the wildcard certificate;
the application never issues per-alias certificates.

## Baseline threat model

| Threat | Required control and evidence |
|---|---|
| Publisher JavaScript, service workers, and browser isolation | Untrusted content is served only from app origins; CSP has non-relaxable `worker-src 'none'`, `object-src 'none'`, `base-uri 'none'`, and `frame-ancestors 'none'`; gateway reserves `/__orcheo/`. |
| Sibling subdomains/cookie tossing/CSRF | No parent-domain cookies. App cookies are host-only `__Host-`, HttpOnly, Secure, SameSite=Lax; mutations require exact Origin, JSON media type, Fetch Metadata, and session CSRF. |
| Authorization-code interception | Gateway keeps state and PKCE verifier server-side; authorization codes are hashed, short-lived, host-bound, PKCE S256 protected, and consumed atomically. |
| Trusted proxies/gateway compromise | Only the gateway service identity can use internal app routes. The backend rejects browser-provided internal headers, user JWTs, ordinary service tokens, workspace headers, workflow ids, actor ids, and asserted client IP. |
| Workflow credentials and cost abuse | Active immutable release snapshots resolve named bindings only; copied executable digests prevent mutable runnable-config fallback. Distributed quota/rate/concurrency leases fail closed for anonymous cost-bearing operations. |
| ZIP/object storage | Staged objects are private and one-time; archive paths, special files, collisions, nested archives, executables, and reserved namespace are rejected. Deployment manifests are server-generated and written last to immutable prefixes. |
| App-data isolation | Repository and database predicates include workspace, app, stable collection, and owner. Reusing a collection display name creates a new stable id, never resurrecting records. |
| Alias reuse and cached state | Release aliases are tombstoned for 30 days. Alias reassignments revoke sessions/codes, invalidate descriptors, and cannot inherit storage or active releases. |
| Global incident response | Durable runtime generation is checked by control plane, gateway, and runtime. Disable invalidates descriptor caches and revokes codes/sessions; authorization fails closed if generation state or required governance is unavailable. |

## Moderation and emergency control

Platform moderation is separate from workspace roles. It requires
`platform:hosted-apps:moderate`; global enable/disable requires
`platform:hosted-apps:runtime-control` plus a stronger-auth policy. Both mutations use
an idempotency key, reason code, restricted reason detail, append-only platform audit,
and one transaction/outbox. Workspace owners and admins are explicitly denied.

Blocks can target an alias, app, workspace, or publisher. The operator who applies a
block may lift it only under the moderation policy; workspace owners may request but
cannot self-reinstate. Public interstitials show a generic unavailable state and abuse
report link, not trusted Studio login UI or private moderation details.

The kill switch increments a durable generation and disables new control mutations,
descriptor resolution, login transactions, code exchange, session introspection, runtime
authorization, and data mutations. Gateway caches have a bounded TTL and consume an
invalidation event; Redis unavailability must not cause a stale allow decision for
authorization or anonymous cost-bearing work. Static delivery may continue only while
the gateway can validate the current durable generation according to the configured SLO.

## Ownership and response targets

The repository owner, **ShaojieJiang**, is the accountable DRI for the initial rollout.
The operating owners below are the authoritative initial assignment.

| Process | Interim accountable owner | Response target |
|---|---|---|
| Publisher verification and allowlist | ShaojieJiang | Verify before enablement; review changes within one business day. |
| Abuse reports and takedown | ShaojieJiang / platform on-call | Acknowledge within four hours; emergency block within one hour. |
| Legal hold | ShaojieJiang with designated legal counsel | Preserve records immediately; confirm case ownership within one business day. |
| Security incident response | ShaojieJiang / security on-call | Page immediately; runtime disable decision within 15 minutes for active exploitation. |

## Local development contract

Use `https://<alias>.apps.localhost` through the configured local Caddy CA or a trusted
development certificate. The feature may use the private filesystem bundle store only in
`local` or explicit `single-node` mode. `VITE_ORCHEO_AUTH_DISABLED=true` is acceptable
for UI iteration but is not evidence for login, cookie, redirect, or CSRF acceptance;
those tests use real authentication over local HTTPS.

## Acceptance record

The implementation must record the production user-content domain, wildcard DNS/TLS
account, moderation on-call rotation, legal-hold contact, and measured revocation SLO
before opening self-service public publishing.
