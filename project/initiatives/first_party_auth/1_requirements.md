# Requirements Document

## METADATA
- **Authors:** Claude (Opus 4.8)
- **Project/Feature Name:** First-party authentication (passwordless email IdP)
- **Type:** Feature
- **Summary:** Replace the Auth0-backed login with a first-party identity provider that issues Orcheo's existing JWT contract, delivering passwordless email (magic link + OTP) sign up and log in, removing all Auth0-specific configuration and code, and switching transactional email from the Resend HTTP integration to SMTP.
- **Owner (if different than authors):** ShaojieJiang
- **Date Started:** 2026-06-21

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Design Doc | `./2_design.md` | ShaojieJiang | First-party Auth Design |
| Project Plan | `./3_plan.md` | ShaojieJiang | First-party Auth Plan |
| Repository Guidelines | `../../../AGENTS.md` | ShaojieJiang | Agents Guidelines |
| Multi-workspace Initiative | `../multi_workspace/1_requirements.md` | ShaojieJiang | Workspace model & membership |
| Backend Auth Package | `apps/backend/src/orcheo_backend/app/authentication/` | ShaojieJiang | JWT/JWKS verification, settings |
| Workspace Repository | `src/orcheo/workspace/repository.py` | ShaojieJiang | Membership + migration primitives |
| Workspace Invitations | `src/orcheo/workspace/service.py` | ShaojieJiang | Invitation accept (email_verified) |
| Studio Auth Feature | `apps/studio/src/features/auth/` | ShaojieJiang | Login UI, OIDC client, session |

## PROBLEM DEFINITION

### Objectives
Stand up a first-party identity provider so Orcheo can authenticate users by email alone, with no dependency on Auth0 (or any external IdP) for the self-serve tier. Deliver passwordless email sign up and log in, and retire all Auth0-specific configuration and code.

### Target users
Self-serve individuals and enterprise end-users on the hosted deployment who sign in with any email domain; self-hosters who need working authentication without standing up or paying for an external IdP. (Enterprise customers wanting central SSO are explicitly served by a *later* initiative — see Non-goals.)

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| New user | sign up with just my email and a link | I can start using Orcheo without creating or managing a password | P0 | `POST /api/auth/email/start` with a new email sends a verification link/OTP; clicking/entering it creates a verified account and an authenticated session |
| Returning user | log in by clicking an emailed link or entering a code | I can get back in without a password | P0 | Same email entry point; a known email yields a session on verification; sessions persist and refresh |
| Invited user | accept a workspace invite after logging in with my email | I join the workspace the invite targeted | P0 | First-party login produces `email_verified=true`; the existing invitation accept flow binds the membership with no Auth0 claim/Action required |
| Any user | stay logged in across reloads and log out everywhere | my session is convenient but revocable | P0 | Refresh issues new access tokens; logout revokes the session server-side; tokens validate against the existing backend auth layer |
| Operator | run Orcheo with no Auth0 account or env vars | the deployment has no external-IdP dependency or cost | P0 | A fresh deployment authenticates end-to-end with only first-party settings; no `VITE_ORCHEO_AUTH_*` Auth0 values or Auth0 tenant are required |
| Existing user (migration) | keep my workspace access after the cutover | I am not locked out when Auth0 is removed | P0 | Memberships are re-keyed from the Auth0 `sub` to an internal user id by verified email; first first-party login resolves my existing memberships |
| Operator | rate-limit and resist abuse of the email entry point | the IdP is not an open email/abuse relay | P1 | Per-IP and per-identity rate limits; constant-time anti-enumeration responses; single-use, short-TTL tokens with attempt lockout |

### Context, Problems, Opportunities
Orcheo currently authenticates via Auth0 over OIDC. The backend is already IdP-agnostic — it validates JWTs against a configurable issuer/JWKS (`apps/backend/src/orcheo_backend/app/authentication/settings.py`) — so Auth0 is only the *issuer* we happen to point at. The dependency is concentrated in (a) the Studio Auth0 OIDC redirect flow and its `VITE_ORCHEO_AUTH_*` configuration, and (b) the operational cost and lock-in of an external IdP for what is, today, email-centric self-serve usage. Because every membership is keyed on the Auth0 `sub`, the lock-in is also a *data* coupling.

The approach is to own the simple self-serve IdP and delegate to external IdPs only for enterprise SSO: a small passwordless email IdP that issues the JWT shape the backend already validates, deferring federation to a later initiative that reuses the retained generic OIDC relying-party (RP) layer.

### Product goals and Non-goals
**Goals**
- First-party, passwordless (magic link + OTP) email sign up and log in.
- A first-party Studio login/signup UI replacing the Auth0 redirect flow.
- An internal `users` identity keyed by verified email; memberships re-keyed off the Auth0 `sub`.
- Clean cutover with **no backward compatibility**: the backend is switched to first-party tokens in a single change — there is no dual-run window, no dual-issuer acceptance of Auth0 and first-party tokens, and no rollback that re-enables Auth0 (rollback is by database restore).
- Complete removal of Auth0-specific env vars, configuration, frontend OIDC client, and the invitation flow's Auth0-claim/Action dependency.
- Switch transactional email (auth challenges and invitations) to **SMTP**, replacing the Resend HTTP integration with a provider-agnostic SMTP sender behind the existing port.
- No regression to the just-shipped workspace invitation flow; `email_verified` now comes from the first-party IdP.

**Non-goals (deferred to later initiatives)**
- Social login (Google/GitHub) — removed here, reintroduced via the SSO initiative.
- Enterprise SSO (OIDC/SAML federation) and SCIM provisioning — the generic OIDC RP layer is *retained* for this future work but is dormant after cutover.
- Passwords (explicitly chosen against — passwordless only).
- MFA/TOTP, account deregistration/deletion, profile management beyond name/email, and per-org auth policy.

## PRODUCT DEFINITION

### Requirements
**P0 — Identity provider core**
- `users` table keyed by a unique, normalized (case-insensitive) email, with `email_verified`, optional display name, status, and timestamps.
- Passwordless email challenge: issue a single-use **magic link** and an equivalent **OTP code** for the same challenge; both verify the same pending record. Short TTL (default 15 min), hashed at rest, attempt-limited.
- A single email entry point that serves both sign up and log in (no separate flows; account is created on first verification).
- Session/token issuance matching the existing JWT contract: signed with `AUTH_JWT_SECRET` (HS256), `sub` = internal user id, plus `email` / `email_verified` / `name` claims; access token + rotating refresh/session.
- Logout (server-side session revocation) and token refresh.

**P0 — Studio login experience**
- First-party login/signup screen: email entry → "check your email" → link click or OTP entry → authenticated session.
- Replace the Auth0 OIDC redirect/callback/auto-login flow; retain route-guard and session-storage behavior backed by the new endpoints.

**P0 — Migration & invitation continuity**
- Backfill `users` from existing membership emails (populated by member-identity capture) and re-key memberships from Auth0 `sub` → internal user id. This requires two **new** repository primitives — an email-indexed membership lookup (`list_memberships_for_email`) and a re-key operation (`reassign_membership`) — added alongside the existing `list_memberships_for_user` / `update_membership_identity` methods, neither of which re-keys the `user_id`.
- Invitation accept continues to work, sourcing `email_verified` from first-party tokens; remove the Auth0 custom-claim/Action requirement and the `/userinfo` fallback added for Auth0.

**P0 — Auth0 decommission**
- Remove Auth0-specific env vars and config from all manifests/docs (frontend `VITE_ORCHEO_AUTH_*` client values; backend Auth0 issuer/JWKS/audience *values*) and delete the Studio Auth0 OIDC client code.
- Retain the generic OIDC RP verification code (`jwks.py`, external-issuer JWT validation) for the future SSO initiative, clearly marked dormant.

**P1 — Abuse resistance & operability**
- Per-IP and per-identity rate limiting (reuse `authentication/rate_limit.py`); constant-time anti-enumeration responses; OTP attempt lockout.
- Email delivery via the shared transactional sender abstraction over **SMTP** (replacing the Resend HTTP sender), with the logging sender retained as the dev default. Shared by auth challenges and invitations.
- Telemetry: signups, logins, verification success/expiry, and delivery failures.

### Designs (if applicable)
See `./2_design.md`. Key screens: email-entry, "check your email" interstitial, OTP-entry fallback, and the post-login redirect (including invitation acceptance).

### [Optional] Other Teams Impacted
- **Workspace/membership:** identity key changes from Auth0 `sub` to internal user id; requires migration and a brief read-path update.
- **Invitations:** `email_verified` source changes; Auth0 Action requirement removed.
- **Self-host / stack templates:** Auth0 env vars removed; new first-party auth + SMTP email settings documented (Resend config retired).

## TECHNICAL CONSIDERATIONS

### Architecture Overview
A new identity service (backend module) owns users, email challenges, and token issuance, signing the same JWT the backend already verifies. The backend `authentication/` package keeps validating tokens — now issued by us via `AUTH_JWT_SECRET` (HS256) instead of fetched from Auth0's JWKS. Studio replaces the Auth0 OIDC client with calls to the new auth endpoints. Membership resolution moves from `sub`-keyed to internal-user-id-keyed via a one-time migration.

### Technical Requirements
- Reuse `AUTH_JWT_SECRET` and existing JWT validation; no new verification path on the hot route.
- Provide an SMTP-backed transactional email sender (replacing the Resend HTTP integration) behind the existing sender port; reuse `authentication/rate_limit.py`.
- Migration is a one-time, idempotent batch backfill run at cutover (create `users` from membership emails, then re-key memberships); because there is no backward compatibility, rollback is by database restore, not by re-enabling Auth0. Existing service-token auth is unaffected.
- Email deliverability becomes a hard dependency for *every* login (consequence of passwordless) — see Risks.

### AI/ML Considerations (if applicable)
N/A.

## MARKET DEFINITION (for products or large features)
N/A — platform infrastructure, no market-launch gating.

## LAUNCH/ROLLOUT PLAN

### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Login success rate (email-start → session) | ≥ 95% of attempts that open the email; validates passwordless UX |
| [Primary] Auth0 dependencies removed | 100% of Auth0 env vars/code removed; deployment boots with none present |
| [Guardrail] Migration completeness | 100% of email-bearing memberships re-keyed; 0 users locked out post-cutover |
| [Guardrail] Median email delivery time | < 30s; deliverability is the passwordless critical path |
| [Secondary] Abuse blocked | Rate-limit/anti-enumeration effective; no enumeration oracle |

### Rollout Strategy
Clean cutover with no backward compatibility: stand up the first-party IdP and Studio UI, run the one-time membership backfill, switch the backend to first-party tokens, and remove Auth0 in the same change. There is no dual-run or dual-issuer window. Staff verify end-to-end on a staging deployment before the production cutover. See `./3_plan.md`.

### Estimated Launch Phases (if applicable)
| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Staging | First-party IdP + Studio UI on staging; staff dogfood signup, login, refresh, logout, invites end-to-end |
| **Phase 2** | Cutover dry-run | Run the membership backfill against a staging copy of production data; verify 100% coverage, no orphaned users |
| **Phase 3** | Production cutover | Single change: backfill memberships, switch backend to first-party tokens, remove Auth0 |

## HYPOTHESIS & RISKS
**Hypothesis:** Email-centric users will complete passwordless login at parity-or-better vs. Auth0, and owning the IdP removes vendor cost/lock-in with no loss of capability for the self-serve tier.

**Risks & mitigations**
- *Email deliverability is now on the login critical path.* Mitigate with a reputable transactional provider, monitoring, OTP fallback, and a documented self-host SMTP requirement.
- *Migration lockout (sub → user re-keying).* Mitigate with member-identity-captured emails, an idempotent batch backfill verified to 100% coverage on a staging copy before cutover, and a database-restore rollback. (No backward compatibility, so there is no dual-run fallback to Auth0 — coverage must be proven before the cutover.)
- *Account-takeover via email compromise* (inherent to passwordless). Mitigate with short-TTL single-use tokens, rate limits, attempt lockout, and session revocation; MFA deferred but noted.
- *Loss of social login* may frustrate Google/GitHub users. Mitigate by migrating them by verified email and signposting the future SSO initiative.

## APPENDIX
- Decision: passwordless (magic link + OTP); passwords explicitly out of scope.
- Decision: retain generic OIDC RP layer (not Auth0-specific) for a future enterprise SSO initiative; remove only Auth0-specific configuration/code.
- Decision: transactional email is delivered via SMTP; the Resend HTTP integration is replaced by an SMTP sender behind the existing port, and the Resend dependency/config is removed.
- Decision: clean cutover with **no backward compatibility** — no dual-run, no dual-issuer acceptance of Auth0 + first-party tokens, and no Auth0 rollback; rollback is by database restore. Existing Auth0 sessions are invalidated at cutover and users re-authenticate via the first-party flow.
- Note: the membership-migration primitives `list_memberships_for_email` / `reassign_membership` are **new** (to be added); the repository today exposes `list_memberships_for_user` / `update_membership_identity`, which do not re-key `user_id`.
