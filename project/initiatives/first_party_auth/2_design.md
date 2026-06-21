# Design Document

## For First-party Authentication (passwordless email IdP)

- **Version:** 0.1
- **Author:** Claude (Opus 4.8)
- **Date:** 2026-06-21
- **Status:** Draft

---

## Overview

This feature replaces Auth0 with a first-party identity provider (IdP) for Orcheo's self-serve tier. The IdP authenticates users by email alone — issuing a single-use magic link and an equivalent OTP code — and mints the **same JWT** the backend already validates, so the hot-path verification logic is unchanged. Auth0 becomes unnecessary: its configuration, env vars, frontend OIDC client, and the invitation flow's Auth0 custom-claim dependency are all removed.

The design is deliberately minimal: own the simple email IdP for self-serve, and keep the generic OIDC relying-party (RP) layer dormant for a later enterprise-SSO initiative. A new internal `users` record keyed by **verified email** becomes the stable identity; workspace memberships are re-keyed from the Auth0 `sub` to this internal id, decoupling Orcheo from any single IdP's subject format for good.

Scope is passwordless email login + signup only. Passwords, social login, enterprise SSO/SAML, MFA, and account deregistration are out of scope (see Requirements doc).

This ships as a **clean cutover with no backward compatibility**: after cutover the backend validates only first-party HS256 tokens, there is no dual-run or dual-issuer window in which Auth0 and first-party tokens are both accepted, existing Auth0 sessions are invalidated, and rollback is by database restore rather than re-enabling Auth0.

## Components

- **Identity Service (FastAPI, new module — `apps/backend/src/orcheo_backend/app/identity/`)**
  - Owns the `users` store, email-challenge issuance/verification, session/refresh tokens, and JWT minting.
  - Mints access tokens signed with `AUTH_JWT_SECRET` (HS256), claims: `sub` = internal user id, `email`, `email_verified`, `name`, `iat`, `exp`, `iss` (first-party issuer).
  - Depends on: the transactional email sender, `authentication/rate_limit.py`, the workspace repository (for membership migration on first login).

- **Backend Auth Verification (existing — `apps/backend/src/orcheo_backend/app/authentication/`)**
  - Unchanged hot path: validates bearer JWTs. Now configured to accept first-party HS256 tokens via `AUTH_JWT_SECRET` and the first-party `iss`/`aud`.
  - `jwt_helpers.extract_identity` keeps reading `email`/`name`; these now originate from first-party tokens. The generic JWKS/external-issuer code (`jwks.py`) is retained but dormant.

- **Transactional Email Sender (existing — `src/orcheo/workspace/email.py`)**
  - The `InvitationEmailSender` abstraction (logging default) is generalized/shared to send auth challenge emails (magic link + OTP).
  - The production implementation is an **SMTP** sender, replacing the Resend HTTP integration. SMTP becomes the sole production transport for both auth challenges and invitations.

- **Workspace Membership & Migration (`src/orcheo/workspace/repository.py`)**
  - Adds two **new** repository primitives — `list_memberships_for_email(email)` and `reassign_membership(workspace_id, from_user_id, to_user_id)` — built on the existing store, which already captures `membership.email` and exposes `list_memberships_for_user` / `update_membership_identity` (neither of which re-keys `user_id`). These drive the `sub` → internal user id backfill.

- **Studio Auth UI (React — `apps/studio/src/features/auth/`)**
  - Replaces the Auth0 OIDC client (`lib/oidc-client.ts`, `pages/oauth-callback.tsx`, `components/auto-login.tsx`) with a first-party email login/signup flow.
  - Retains `components/require-auth.tsx` (route guard) and `lib/auth-session.ts` (token storage/refresh), repointed at first-party endpoints.

## Request Flows

### Flow 1: Sign up / Log in (magic link)
1. User enters email on the Studio login screen → `POST /api/auth/email/start { email, intent }`.
2. Identity Service normalizes the email, finds-or-stages a user, creates a challenge (random token + 6–8 digit OTP), stores **hashes** with a 15-min TTL, and emails the magic link (`/auth/verify?token=…`) plus the OTP. Response is constant-time regardless of whether the email exists (anti-enumeration).
3. User clicks the link → Studio calls `POST /api/auth/email/verify { token }`.
4. Identity Service validates the token (unconsumed, unexpired, attempt-OK), marks the user `email_verified=true`, consumes the challenge, creates a session, and returns access + refresh tokens and the profile.
5. On login the service resolves the user's memberships by verified email — they were re-keyed to the internal user id by the one-time backfill (Flow 4) — before returning.
6. Studio stores tokens and routes the user in.

### Flow 2: OTP fallback
1. Same `email/start` as above.
2. User enters the OTP in Studio → `POST /api/auth/email/verify { email, code }`.
3. Same verification, lockout after N failed attempts; success path identical to Flow 1 step 4+.

### Flow 3: Session refresh & logout
1. Access token nears expiry → `POST /api/auth/refresh` with the refresh token → new access token; refresh token rotated.
2. `POST /api/auth/logout` revokes the server-side session/refresh token; Studio clears local tokens.

### Flow 4: Membership migration (one-time backfill at cutover)
1. A one-time, idempotent backfill creates a `users` row (new UUID) for each distinct `membership.email`, then for every membership still keyed by an Auth0 `sub` calls `reassign_membership(workspace_id, from_user_id=sub, to_user_id=user_id)` to move it to that internal user id via `list_memberships_for_email(email)` (preserves role and row; skips rows already on an internal id).
2. After cutover, first-party login resolves the pre-created `users` row by verified email, so its memberships already point at the matching internal id. The resolver cache is invalidated so identities resolve their workspaces immediately.
3. Because there is no backward compatibility (no dual-run), no new Auth0-keyed rows are created after cutover; any stragglers are caught by re-running the idempotent backfill.

### Flow 5: Invitation acceptance (continuity)
1. Invitee logs in via Flow 1/2 → holds a first-party token with `email_verified=true`.
2. Studio `/invitations/accept` calls the existing accept endpoint; `_verified_email` reads the first-party claims directly (no Auth0 `/userinfo` fallback, no custom-claim Action).
3. Membership is created/bound to the internal user id as today.

## API Contracts

```
POST /api/auth/email/start
Body: { email: string, intent: "login" | "signup" }   # intent advisory only
Response:
  200 OK -> { status: "sent" }      # constant-time; never reveals account existence
  429    -> rate limited

POST /api/auth/email/verify
Body: { token: string } | { email: string, code: string }
Response:
  200 OK -> { access_token, refresh_token, expires_in, user: { id, email, email_verified, name } }
  400/410 -> invalid/expired/consumed challenge
  423    -> too many attempts (locked)

POST /api/auth/refresh
Body: { refresh_token: string }
Response:
  200 OK -> { access_token, refresh_token, expires_in }
  401    -> invalid/revoked refresh token

POST /api/auth/logout
Headers: Authorization: Bearer <access_token>
Response: 204 No Content    # revokes the session/refresh token

GET /api/auth/me
Headers: Authorization: Bearer <access_token>
Response: 200 OK -> { id, email, email_verified, name }

# Access tokens carry the existing contract validated by authentication/:
# { sub: <user uuid>, email, email_verified, name, iss: <first-party>, aud, iat, exp }
```

## Data Models / Schemas

**`users`**

| Field | Type | Description |
|-------|------|-------------|
| id | UUID (PK) | Internal stable identity; becomes membership key |
| email | TEXT (unique, normalized lower-case) | Verified email; the human identity |
| email_verified | BOOL | True once any challenge is completed |
| name | TEXT (nullable) | Optional display name |
| status | TEXT | `active` \| `disabled` |
| created_at / last_login_at | TIMESTAMPTZ | Lifecycle timestamps |

**`auth_email_challenges`**

| Field | Type | Description |
|-------|------|-------------|
| id | UUID (PK) | Challenge id |
| email | TEXT | Target email (user may not exist yet) |
| token_hash | TEXT | SHA-256 of the magic-link token (raw never stored) |
| code_hash | TEXT | Hash of the OTP code |
| purpose | TEXT | `login_or_signup` |
| attempts | INT | OTP attempt counter for lockout |
| expires_at | TIMESTAMPTZ | Short TTL (default 15 min) |
| consumed_at | TIMESTAMPTZ (nullable) | Single-use marker |

**`auth_sessions`** (refresh tokens)

| Field | Type | Description |
|-------|------|-------------|
| id | UUID (PK) | Session id |
| user_id | UUID (FK users) | Owner |
| refresh_token_hash | TEXT | Hashed, rotating |
| expires_at / revoked_at | TIMESTAMPTZ | Lifecycle / logout |
| user_agent / ip | TEXT (nullable) | Audit context |

**Membership change:** `workspace_memberships.user_id` transitions from the Auth0 `sub` string to the internal `users.id` (UUID as text). Migration is data-only; the column and the `(workspace_id, user_id)` uniqueness constraint are unchanged. The one-time backfill assigns each email exactly one internal id, so a workspace cannot end up with two rows for the same person; if a target `(workspace_id, internal_id)` row already exists, the backfill keeps it and drops the duplicate `sub`-keyed row (highest role wins).

## Security Considerations

- **Token handling:** magic-link tokens and OTPs are random, single-use, short-TTL, and stored only as hashes. Verification is constant-time where it matters.
- **Anti-enumeration:** `email/start` returns an identical response and timing whether or not the account exists.
- **Rate limiting & lockout:** per-IP and per-identity limits via `authentication/rate_limit.py`; OTP attempts capped with lockout.
- **Session security:** rotating refresh tokens, server-side revocation, secure/httpOnly storage; access-token TTL kept short.
- **Signing key:** `AUTH_JWT_SECRET` is the IdP signing key — document rotation; consider moving to RS256/JWKS later if external verifiers need it (not required now).
- **email_verified semantics:** only set true via a completed challenge; this is the control the invitation flow relies on.
- **Unaffected:** service-token (machine) auth is independent and untouched.

## Performance Considerations
- Hot path (request authorization) is unchanged — local HS256 verification, no network call (removes the Auth0 JWKS fetch/cache entirely).
- Email send is async to the user's perception via the "check your email" interstitial; sender failures surfaced and retried.
- Challenge/session tables are small and indexed by `email` and `token_hash`.

## Testing Strategy
- **Unit tests:** email normalization; challenge issuance/verification (expiry, single-use, OTP lockout); JWT minting/claims; refresh rotation/revocation; anti-enumeration response invariance.
- **Integration tests:** full start→verify→session; refresh; logout; invitation accept end-to-end on first-party tokens; membership migration (`sub` → user id) idempotency.
- **Manual QA:** Studio signup, login (link + OTP), reload/refresh, logout, invite acceptance, and a fresh deployment with zero Auth0 env vars.

## Rollout Plan
This initiative ships as a **clean cutover with no backward compatibility** — there is no dual-run, no dual-issuer window, and no rollback that re-enables Auth0.
1. **Phase 1 — staging:** ship Identity Service + Studio UI to staging; staff dogfood signup, login (link + OTP), refresh, logout, and invitation acceptance end-to-end.
2. **Phase 2 — cutover dry-run:** run the one-time membership backfill against a staging copy of production data; verify 100% of email-bearing memberships re-key and no user is orphaned.
3. **Phase 3 — production cutover (single change):** run the backfill, switch the backend from the Auth0 issuer/JWKS to first-party HS256 tokens, and remove Auth0 env vars/code, the Studio OIDC client, the invitation Auth0-claim path, and the Resend integration/config (SMTP becomes the sole transport). Keep the generic OIDC RP code dormant for the SSO initiative.

**No backward compatibility:** after cutover the backend validates only first-party HS256 tokens — it never accepts Auth0 and first-party tokens concurrently. Any Auth0 session is invalidated at cutover and users re-authenticate via the first-party flow. Rollback is by database restore, not by re-enabling Auth0.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-06-21 | Claude (Opus 4.8) | Initial draft |
| 2026-06-21 | Claude (Opus 4.8) | Clean cutover, no backward compatibility (removed dual-run/dual-issuer); corrected membership-migration primitives as new (`list_memberships_for_email` / `reassign_membership`); added re-key conflict handling |
