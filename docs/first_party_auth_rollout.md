# First-party authentication: rollout & operations

Orcheo authenticates users with a **first-party, passwordless email identity
provider** (magic link + OTP). There is no Auth0 dependency. This page covers
the production cutover, signing-key rotation, and the future move to
RS256/JWKS.

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

## Rollout phases

1. **Phase 1 — staging dogfood.** Deploy the identity service + Studio UI to
   staging. Staff verify signup, login (link **and** OTP), reload/refresh,
   logout, and invitation acceptance end-to-end.
2. **Phase 2 — cutover dry-run.** Restore a copy of production data to staging
   and exercise signup, login, refresh, logout, and invitation acceptance
   against it. Confirm no user is orphaned.
3. **Phase 3 — production cutover (single change).** Switch the backend to
   first-party tokens (`ORCHEO_AUTH_JWT_SECRET` / `ORCHEO_AUTH_ISSUER` /
   `ORCHEO_AUTH_AUDIENCE` set, no `ORCHEO_AUTH_JWKS_URL`), and remove the Auth0
   env/tenant. Users re-authenticate via the first-party flow.

**Rollback.** There is no dual-run fallback. If the cutover must be reverted,
restore the database from the pre-cutover backup. Validate the full auth flow on
a staging copy (Phase 2) before touching production.

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

## Passkeys

Signed-in users can add passkeys (WebAuthn credentials) from **Profile →
Passkeys**, or from the offer Studio shows after an emailed-code sign-in on a
device that can create one. Passkeys are a second way to sign in to an existing
account, not a separate one:

- **Sign-in is usernameless.** Studio offers saved passkeys in the email
  field's autofill and behind a "Sign in with a passkey" button. Nothing about
  the account is sent first, so `/api/auth/passkey/login/options` reveals
  nothing about which accounts exist. A verified passkey ends in the same
  session and tokens as an emailed code.
- **Email stays the root of identity.** Accounts are still created only by
  email verification, and emailed codes keep working as the recovery path, so
  removing a passkey can never lock anyone out. This also means passkeys are not
  a second factor: anyone who can read the mailbox can still sign in.
- **Adding or removing a passkey needs a recent sign-in.** Access tokens carry
  `auth_time` (when the session signed in, kept across refreshes). Either action more
  than 10 minutes after signing in returns
  `403 auth.reauthentication_required`, and Studio asks the user to confirm
  with an emailed code. Each added or removed passkey also emails a security
  notice to the account owner.
  Renaming only changes a display label and does not require a recent sign-in.
- **Passkeys survive sign-out.** "Sign out" revokes sessions, not passkeys;
  users remove passkeys from their profile.

**Configuration.** The relying party is derived from `ORCHEO_STUDIO_URL`: its
host is the RP ID and its origin is the only origin allowed to use passkeys,
because the browser reports the origin of the Studio page. Override with
`ORCHEO_AUTH_WEBAUTHN_RP_ID` and `ORCHEO_AUTH_WEBAUTHN_ORIGINS` (see
[Environment Variables](environment_variables.md)). Origins are matched
exactly, never by suffix, so other subdomains of the RP ID (such as hosted
apps) cannot complete a passkey ceremony. Browsers only allow passkeys over
https or on `http://localhost` (not `127.0.0.1`), and never for IP addresses;
with such a `ORCHEO_STUDIO_URL` the backend turns passkeys off and Studio shows
only emailed-code sign-in.

**Choose the RP ID once.** Passkeys are bound to the RP ID they were created
for. Changing the RP ID (including by moving Studio to another host) makes
every registered passkey stop working; users then sign in with an emailed code
and add new passkeys.

**Storage.** Passkeys live in `auth_passkeys` (public keys only) and ceremony
challenges in `auth_passkey_challenges`. Challenges are single-use, expire after
five minutes, and are consumed before a response is verified, so they work
across backend replicas. Both stores purge expired challenges whenever a new
challenge is inserted. Both tables are created automatically; deleting a user
deletes their passkeys. Registration completion rechecks account status and the
email-domain allowlist before saving a credential.

**Upgrade compatibility.** Release the new core identity models before the
backend that imports them; the backend requires `orcheo>=0.45.9`. Existing access
tokens without `auth_time` require an emailed-code confirmation before adding or
removing a passkey. The WebAuthn dependency upgrades the locked `cryptography`
from 46 to 50. Intel macOS wheels were removed in
[cryptography 49](https://cryptography.io/en/49.0.0/changelog/), so native backend
installs on Intel Macs require a source build with the
[documented build tools](https://cryptography.io/en/50.0.2/installation/).
Docker deployments and the arm64 desktop build are unaffected.

**Local testing.** Open Studio at `http://localhost:2026` and use Chrome
DevTools → More tools → WebAuthn to add a virtual authenticator (CTAP2,
internal, resident keys and user verification enabled).

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
