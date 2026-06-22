# Project Plan

## For First-party Authentication (passwordless email IdP)

- **Version:** 0.1
- **Author:** Claude (Opus 4.8)
- **Date:** 2026-06-21
- **Status:** Draft

---

## Overview

Build a first-party, passwordless email identity provider that mints Orcheo's existing JWT contract, replace the Studio Auth0 login with a first-party UI, migrate workspace memberships from the Auth0 `sub` to an internal verified-email identity, switch transactional email to SMTP, and decommission all Auth0-specific configuration and code (and the Resend integration). This is a **clean cutover with no backward compatibility** — no dual-run, no dual-issuer window, and no rollback that re-enables Auth0 (rollback is by database restore). Scope is email magic-link + OTP login/signup only; social login, enterprise SSO, MFA, and deregistration are out of scope.

**Related Documents:**
- Requirements: `./1_requirements.md`
- Design: `./2_design.md`

---

## Milestones

### Milestone 1: Identity Service core

**Description:** A backend identity service that issues and verifies passwordless email challenges and mints first-party JWTs validated by the existing auth layer. Success: an automated test can drive start → verify → authenticated session and refresh/logout, with tokens accepted by `authentication/`.

#### Task Checklist

- [x] Task 1.1: Add `users`, `auth_email_challenges`, and `auth_sessions` schemas + repository (in-memory + Postgres), mirroring the workspace store pattern.
  - Dependencies: None
- [x] Task 1.2: Implement email-challenge issuance (magic-link token + OTP, hashed, TTL, single-use) and verification with attempt lockout.
  - Dependencies: Task 1.1
- [x] Task 1.3: Implement JWT minting (HS256 via `AUTH_JWT_SECRET`, first-party `iss`/`aud`, `sub`=user id, `email`/`email_verified`/`name`) + refresh-token rotation and session revocation.
  - Dependencies: Task 1.1
- [x] Task 1.4: Generalize the transactional email sender (`src/orcheo/workspace/email.py`) to send auth challenge emails, and add an **SMTP**-backed sender as the production implementation (replacing Resend) behind the existing port; keep the logging sender as the dev default.
  - Dependencies: None
- [x] Task 1.5: Expose endpoints `POST /api/auth/email/start`, `/email/verify`, `/refresh`, `/logout`, `GET /api/auth/me` with rate limiting and anti-enumeration.
  - Dependencies: Tasks 1.2, 1.3
- [x] Task 1.6: Configure `authentication/` to validate first-party HS256 tokens (issuer/audience/secret) as the sole accepted issuer — no dual-issuer/Auth0 acceptance; keep the generic JWKS code present but dormant for the future SSO initiative.
  - Dependencies: Task 1.3

---

### Milestone 2: Studio first-party login experience

**Description:** Replace the Auth0 OIDC redirect flow with a first-party email login/signup UI, preserving route-guarding, session storage, and refresh. Success: a user can sign up, log in (link + OTP), persist across reload, and log out entirely against the new endpoints.

#### Task Checklist

- [x] Task 2.1: Build the email-entry → "check your email" → OTP-entry screens and the `/auth/verify` link handler.
  - Dependencies: Milestone 1
- [x] Task 2.2: Repoint `lib/auth-session.ts` (token storage/refresh) and `components/require-auth.tsx` (guard) at first-party endpoints.
  - Dependencies: Task 2.1
- [x] Task 2.3: Remove the Auth0 OIDC client flow (`lib/oidc-client.ts`, `pages/oauth-callback.tsx`, `components/auto-login.tsx`) and rewrite `pages/login.tsx`.
  - Dependencies: Task 2.2
- [x] Task 2.4: Verify the invitation `/invitations/accept` route works on first-party tokens end-to-end.
  - Dependencies: Task 2.2

---

### Milestone 3: Identity migration & invitation continuity

**Description:** Re-key workspace memberships from the Auth0 `sub` to the internal user id by verified email, and source `email_verified` for invitations from first-party tokens. Success: 100% of email-bearing memberships migrate idempotently; no user is locked out; invitations no longer require any Auth0 claim/Action.

#### Task Checklist

- [x] Task 3.1: Add the two **new** repository primitives — `list_memberships_for_email(email)` and `reassign_membership(workspace_id, from_user_id, to_user_id)` (the existing `list_memberships_for_user` / `update_membership_identity` do not re-key `user_id`); invalidate the resolver cache on re-key.
  - Dependencies: Milestone 1
- [x] Task 3.2: Implement the one-time idempotent batch backfill (CLI/management task) run at cutover: create a `users` row per distinct captured `membership.email`, then re-key every `sub`-keyed membership to the internal id via `reassign_membership` (resolving any `(workspace_id, internal_id)` collision by keeping the existing row, highest role wins). Because there is no dual-run, this is the primary migration path, not a straggler cleanup.
  - Dependencies: Task 3.1
- [x] Task 3.3: Simplify the invitation accept path — read `email_verified` from first-party claims; remove the Auth0 `/userinfo` fallback and custom-claim handling in `routers/workspaces.py::_verified_email`.
  - Dependencies: Milestone 2
- [x] Task 3.4: Report on migration coverage (memberships migrated vs. remaining) for cutover readiness.
  - Dependencies: Task 3.2

---

### Milestone 4: Auth0 & Resend decommission

**Description:** Remove every Auth0-specific dependency, and retire the Resend integration in favour of SMTP, once migration coverage is sufficient. Success: a fresh deployment authenticates end-to-end and sends email over SMTP with no Auth0 env vars/tenant/code and no Resend config present; the generic OIDC RP layer remains, clearly marked dormant.

#### Task Checklist

- [x] Task 4.1: Remove Auth0 frontend env/config (`VITE_ORCHEO_AUTH_ISSUER/CLIENT_ID/AUDIENCE/ORGANIZATION/PROVIDER_*/REDIRECT_URI/SCOPES/STATE_BYTES/VERIFIER_BYTES`) from manifests, `.env` examples, and stack templates. _(Done in `docker-compose.yml`, `deploy/stack/docker-compose.yml`, `deploy/stack/.env.example`, `studio-entrypoint.sh`, docs. The `orcheo install` SDK wizard still writes them — flagged as a follow-up task.)_
  - Dependencies: Milestone 2
- [x] Task 4.2: Remove the Auth0 backend issuer/JWKS/audience *values* (there is no dual-issuer config — the backend accepts only the first-party issuer); retain the generic JWKS code marked dormant for the SSO initiative. _(Backend issuer/audience are generic config; first-party-only validation enforced; `jwks.py` marked dormant; removed Auth0 namespaced-claim handling from `jwt_helpers.py`.)_
  - Dependencies: Milestone 3
- [x] Task 4.3: Delete the Auth0 Action / custom-claim documentation and references introduced for the invitation flow. _(Deleted `docs/auth0_idp_setup.md` + mkdocs nav entry; removed namespaced-claim code.)_
  - Dependencies: Task 3.3
- [x] Task 4.4: Update `AGENTS.md`, self-host docs, and stack templates to document first-party auth + SMTP email settings.
  - Dependencies: Tasks 4.1, 4.2
- [x] Task 4.5: Remove the Resend integration and its config/env (e.g. `ORCHEO_RESEND_API_KEY`, `ORCHEO_INVITE_FROM_EMAIL`) and the `httpx`-based Resend sender; SMTP becomes the sole production transport. _(Removed from `src/orcheo/workspace/email.py` + backend dependencies. The `orcheo install` SDK wizard still writes the Resend env keys — flagged as a follow-up task.)_
  - Dependencies: Task 1.4
- [x] Task 4.6: Update stack templates and `.env` examples to replace Resend settings with SMTP settings (host, port, credentials, from-address, TLS).
  - Dependencies: Task 4.5

---

### Milestone 5: Hardening & rollout

**Description:** Production-readiness for the passwordless critical path and a safe clean cutover (no backward compatibility). Success: rate limits and anti-enumeration verified, email delivery monitored, and the production cutover completed with a database-restore rollback path.

#### Task Checklist

- [x] Task 5.1: Finalize per-IP/per-identity rate limits, OTP lockout, and anti-enumeration timing; add abuse tests.
  - Dependencies: Milestone 1
- [x] Task 5.2: Add telemetry/alerts for signups, logins, verification expiry, and email-delivery failures.
  - Dependencies: Milestone 1
- [x] Task 5.3: Clean-cutover rollout — staging dogfood (Phase 1) → cutover dry-run against a staging copy verifying 100% backfill coverage (Phase 2) → single-change production cutover (Phase 3). No dual-run; document database-restore rollback (not re-enabling Auth0). _(Documented in `docs/first_party_auth_rollout.md`; cutover execution is an operational step.)_
  - Dependencies: Milestones 2, 3
- [x] Task 5.4: Document `AUTH_JWT_SECRET` rotation and the optional future move to RS256/JWKS.
  - Dependencies: Task 1.3

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-06-21 | Claude (Opus 4.8) | Initial draft |
| 2026-06-21 | Claude (Opus 4.8) | Clean cutover, no backward compatibility (removed dual-run/dual-issuer/Auth0 rollback); corrected migration tasks to add new `list_memberships_for_email` / `reassign_membership` primitives |
