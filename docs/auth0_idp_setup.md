# Auth0 IdP Setup for Orcheo (Docker Stack)

Author: ShaojieJiang

This guide shows how to configure Auth0 as the OAuth/OIDC identity provider for Orcheo Canvas + backend when running the stack from `deploy/stack/docker-compose.yml`.

## Goal

Set `ORCHEO_AUTH_MODE=required` and allow authenticated Canvas users to:

- load workflows
- start ChatKit workflow sessions
- use shared ChatKit session flows

## Prerequisites

- An Auth0 account with dashboard access.
- Your Orcheo stack running from `deploy/stack`.
- A target Canvas URL (for example `http://localhost:5173` in local dev).
- A target backend URL (for example `http://localhost:8000` in local dev).

## 1. Create the Auth0 API

1. In Auth0, go to `Applications > APIs > Create API`.
2. Set:
   - `Name`: `Orcheo API` (or similar)
   - `Identifier`: your API audience (example: `https://orcheo-api`)
   - `Signing Algorithm`: `RS256`
3. Open the API `Permissions` tab and add:
   - `workflows:read` — `List and view workflow definitions.`
   - `workflows:execute` — `Trigger workflow executions (including workflow-scoped ChatKit session startup in Canvas).`
   - `chatkit:session` — `Issue signed ChatKit session tokens for authenticated users.`

## 2. Configure API access policy and RBAC

In the same API:

1. Open `Application Access`.
2. For **User Access**, choose one:
   - `Allow` (simpler for development)
   - `Allow via client-grant` (stricter production option; then explicitly authorize your SPA app and selected permissions)
3. In **User Access permissions** for your Canvas SPA app, explicitly add:
   - `workflows:read`
   - `workflows:execute`
   - `chatkit:session`

Critical: even when Canvas requests these scopes, Auth0 will not include them in the access token unless API user access/client-grant permissions allow them for that application.

For RBAC:

- If **RBAC disabled**: requested scopes can be granted directly.
- If **RBAC enabled**: assign permissions to users via roles; `scope` becomes the intersection of requested scopes and assigned permissions.

## 3. Create the SPA application

1. Go to `Applications > Applications > Create Application`.
2. Choose `Single Page Web Applications`.
3. In app settings, set at least:
   - `Allowed Callback URLs`:
     - `http://localhost:5173/auth/callback`
     - `https://<your-canvas-domain>/auth/callback`
   - `Allowed Logout URLs`:
     - `http://localhost:5173`
     - `https://<your-canvas-domain>`
   - `Allowed Web Origins`:
     - `http://localhost:5173`
     - `https://<your-canvas-domain>`
   - `Allowed Origins (CORS)`:
     - `http://localhost:5173`
     - `https://<your-canvas-domain>`
4. Save, then note:
   - `Domain` (issuer base, example: `your-auth0-domain.us.auth0.com`)
   - `Client ID`

## 4. Create an organization (optional but recommended for B2B)

Only required if you will set `VITE_ORCHEO_AUTH_ORGANIZATION`.

1. Go to `Organizations > Create Organization`.
2. Create an org (for example slug `orcheo-team`).
3. Enable at least one connection for that org (Database, Google, GitHub, Enterprise, etc.).
4. Add users/members (or enable auto-membership for supported flows).
5. In your Orcheo env, set `VITE_ORCHEO_AUTH_ORGANIZATION` to the org identifier you use for Auth0 login (commonly `org_...`).

## 5. Map Auth0 config to Orcheo stack env vars

Update your stack `.env` (derived from `deploy/stack/.env.example`):

### Backend auth validation

- `ORCHEO_AUTH_MODE=required`
- `ORCHEO_AUTH_JWKS_URL=https://<auth0-domain>/.well-known/jwks.json`
- `ORCHEO_AUTH_AUDIENCE=<auth0-api-identifier>`
- `ORCHEO_AUTH_ISSUER=https://<auth0-domain>/`
- `ORCHEO_AUTH_DEV_LOGIN_ENABLED=false`
- `ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN=<long-random-secret>`

### Canvas OAuth client settings

- `VITE_ORCHEO_BACKEND_URL=<public-backend-url>`
- `VITE_ORCHEO_AUTH_ISSUER=https://<auth0-domain>/`
- `VITE_ORCHEO_AUTH_CLIENT_ID=<auth0-spa-client-id>`
- `VITE_ORCHEO_AUTH_REDIRECT_URI=` (leave blank to use `${origin}/auth/callback`, or set explicitly)
- `VITE_ORCHEO_AUTH_SCOPES=openid profile email workflows:read workflows:execute chatkit:session`
- `VITE_ORCHEO_AUTH_AUDIENCE=<auth0-api-identifier>`
- `VITE_ORCHEO_AUTH_ORGANIZATION=<org_id>` (optional)
- `VITE_ORCHEO_AUTH_PROVIDER_PARAM=connection` (optional; for direct provider hints)
- `VITE_ORCHEO_AUTH_PROVIDER_GOOGLE=google-oauth2` (optional)
- `VITE_ORCHEO_AUTH_PROVIDER_GITHUB=github` (optional)
- `VITE_ORCHEO_AUTH_PROVIDER_SIGNUP=Username-Password-Authentication` (optional; use a database connection or other sign-up target instead of a social login)

### Related runtime settings frequently needed

- `ORCHEO_CORS_ALLOW_ORIGINS=<canvas-origin(s)>`
- `ORCHEO_CHATKIT_TOKEN_SIGNING_KEY=<long-random-secret>`
- `VITE_ORCHEO_CHATKIT_DOMAIN_KEY=<chatkit-domain-public-key>`

## 6. Restart and verify

1. Restart stack services (`docker compose up -d` from `deploy/stack`).
2. Log out of Canvas and log in again (to refresh tokens with new scopes).
3. Verify access token claims:
   - `aud` contains your API audience.
   - `scope` contains `workflows:read workflows:execute chatkit:session`.
4. Open a workflow ChatKit window in Canvas.

## Troubleshooting checklist

- `401 Missing required scopes`: requested scopes in Canvas env do not match API permissions or token lacks those scopes.
- `401 Workflow access denied for caller.`: token scopes are valid, but the caller is not authorized for the specific workflow. For untagged workflows, Orcheo checks workflow owner against the token `sub`. For workspace-tagged workflows (`workspace:<id>`), Orcheo checks workspace claim intersection. Recreate the workflow as the logged-in user, or align workflow tags and workspace claims.
- `401/403` with org enabled: user is not a member of the specified organization or org/connection setup is incomplete.
- `invalid_token` issuer/audience: `ORCHEO_AUTH_ISSUER` and `ORCHEO_AUTH_AUDIENCE` mismatch Auth0 token values.
- ChatKit session issuance errors unrelated to OAuth: missing `ORCHEO_CHATKIT_TOKEN_SIGNING_KEY`.

## Auth0 docs used

- [Register Single-Page Web Applications](https://auth0.com/docs/get-started/auth0-overview/create-applications/single-page-web-apps)
- [Application Settings](https://auth0.com/docs/get-started/applications/application-settings)
- [Add API Permissions](https://auth0.com/docs/get-started/apis/add-api-permissions)
- [Enable Role-Based Access Control for APIs](https://auth0.com/docs/get-started/apis/enable-role-based-access-control-for-apis)
- [API Access Policies for Applications](https://auth0.com/docs/get-started/apis/api-access-policies-for-applications)
- [Create Your First Organization](https://auth0.com/docs/organizations/create-first-organization)
- [Login Flows for Organizations](https://auth0.com/docs/manage-users/organizations/login-flows-for-organizations)
- [Enable Organization Connections](https://auth0.com/docs/manage-users/organizations/configure-organizations/enable-connections)
- [Assign Roles to Users](https://auth0.com/docs/users/assign-roles-to-users)
