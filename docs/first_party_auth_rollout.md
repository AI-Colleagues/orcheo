# First-party authentication: rollout & operations

Orcheo authenticates users with a **first-party, passwordless email identity
provider** (magic link + OTP). There is no Auth0 dependency. This page covers
the production cutover, the membership migration, signing-key rotation, and the
future move to RS256/JWKS.

## Overview

- The identity service mints HS256 access tokens signed with
  `ORCHEO_AUTH_JWT_SECRET`, validated by the backend auth layer as the **sole**
  accepted issuer (`ORCHEO_AUTH_ISSUER`). See
  [Environment Variables](environment_variables.md) for all settings.
- Transactional email (sign-in links/codes and workspace invitations) is sent
  over **SMTP** (`ORCHEO_SMTP_*`). With no SMTP host configured the backend logs
  the link/code instead — fine for local/dev, not for production.
- This is a **clean cutover with no backward compatibility**: after cutover the
  backend validates only first-party tokens, there is no dual-run or
  dual-issuer window, existing Auth0 sessions are invalidated, and rollback is
  by **database restore** (not by re-enabling Auth0).

## Membership migration (Auth0 `sub` → internal user id)

Every workspace membership is keyed by the Auth0 `sub` before cutover. The
one-time, **idempotent** backfill creates a `users` row per distinct captured
`membership.email` and re-keys each membership onto the internal user id.

```bash
# Report coverage without changing anything (cutover readiness).
python -m orcheo.identity.cli coverage      # or: orcheo-identity-migrate coverage

# Run the idempotent backfill.
python -m orcheo.identity.cli backfill      # or: orcheo-identity-migrate backfill
```

Both build the Postgres-backed stores from `ORCHEO_POSTGRES_DSN`. The backfill
resolves any `(workspace_id, internal_id)` collision by keeping the existing row
(highest role wins) and dropping the duplicate `sub`-keyed row. Re-running makes
no further changes once memberships already point at internal ids. `coverage`
exits non-zero while any email-bearing membership remains unmigrated.

## Rollout phases

1. **Phase 1 — staging dogfood.** Deploy the identity service + Studio UI to
   staging. Staff verify signup, login (link **and** OTP), reload/refresh,
   logout, and invitation acceptance end-to-end.
2. **Phase 2 — cutover dry-run.** Restore a copy of production data to staging
   and run `orcheo-identity-migrate coverage`, then `backfill`, then `coverage`
   again. Confirm 100% of email-bearing memberships re-key and no user is
   orphaned.
3. **Phase 3 — production cutover (single change).** Run the backfill, switch
   the backend to first-party tokens (`ORCHEO_AUTH_JWT_SECRET` /
   `ORCHEO_AUTH_ISSUER` / `ORCHEO_AUTH_AUDIENCE` set, no `ORCHEO_AUTH_JWKS_URL`),
   and remove the Auth0 env/tenant. Users re-authenticate via the first-party
   flow.

**Rollback.** There is no dual-run fallback. If the cutover must be reverted,
restore the database from the pre-cutover backup. Prove migration coverage on a
staging copy (Phase 2) before touching production.

## `ORCHEO_AUTH_JWT_SECRET` rotation

`ORCHEO_AUTH_JWT_SECRET` is the IdP signing key. Rotating it invalidates all
outstanding **access** tokens (they fail signature verification); refresh tokens
are stored server-side as hashes and are unaffected, so clients transparently
obtain new access tokens on their next `/api/auth/refresh`.

To rotate:

1. Generate a new secret, e.g. `openssl rand -hex 32`.
2. Set `ORCHEO_AUTH_JWT_SECRET` to the new value across backend, worker, and
   beat, then restart them together.
3. Existing access tokens are rejected immediately; Studio refreshes silently.
   To force full re-authentication instead, also revoke sessions (operators can
   clear `auth_sessions`).

Because HS256 is symmetric, there is no overlap window where both the old and
new secret validate — rotate during a low-traffic window, or accept a brief
wave of refreshes. Keep the secret only in the secret store; never commit it.

## Future: RS256 / JWKS (optional)

The backend retains a generic, **dormant** OIDC relying-party layer
(`authentication/jwks.py`, `ORCHEO_AUTH_JWKS_URL`). If external services ever
need to verify Orcheo tokens without sharing the symmetric secret, the identity
service can move to **RS256** and publish a JWKS document; the backend would
then validate via the existing JWKS path instead of `ORCHEO_AUTH_JWT_SECRET`.
This is not required today and is reserved for the future enterprise-SSO
initiative, which reuses the same dormant layer for OIDC/SAML federation.

## Telemetry

The identity service records events on the shared auth telemetry sink:
`auth.challenge_sent`, `auth.signup`, `auth.login`, `auth.verify_expired`, and
`auth.email_delivery_failure`. Alert on a rising `auth.email_delivery_failure`
rate (deliverability is the passwordless critical path) and on anomalous
`auth.verify_expired` / rate-limit (`429`) volume.
