# Authentication Setup and Usage Guide

This guide provides comprehensive instructions for setting up and using authentication in Orcheo. Authentication protects your workflow orchestration platform by ensuring that only authorized users and services can access your workflows, data, and API endpoints.

## Table of Contents

- [Overview](#overview)
- [Authentication Modes](#authentication-modes)
- [Service Token Authentication](#service-token-authentication)
- [JWT Authentication](#jwt-authentication)
- [WebSocket Authentication](#websocket-authentication)
- [Authorization and Scopes](#authorization-and-scopes)
- [Rate Limiting](#rate-limiting)
- [Security Best Practices](#security-best-practices)
- [Troubleshooting](#troubleshooting)

## Overview

Orcheo supports a flexible authentication system that can work in three modes:

- **Disabled**: No authentication required (development only)
- **Optional**: Authentication is validated when provided but not required
- **Required**: All requests must include valid credentials

The authentication system supports two primary methods:

1. **Service Tokens**: Long-lived tokens for CLI, CI/CD, and service-to-service communication
2. **JWT Tokens**: Standards-based JSON Web Tokens with symmetric (HS256) or asymmetric (RS256) signing

For interactive users, JWTs are minted by Orcheo's **first-party passwordless
email identity provider** (magic link + OTP): the identity service signs HS256
access tokens with `ORCHEO_AUTH_JWT_SECRET` and the backend accepts only the
configured `ORCHEO_AUTH_ISSUER`. See
[First-party authentication: rollout & operations](first_party_auth_rollout.md)
for the production setup; the generic JWKS/RS256 material below is retained for
the dormant external-IdP path.

## Authentication Modes

### Setting the Authentication Mode

Configure authentication mode via the `ORCHEO_AUTH_MODE` environment variable:

```bash
# Disable authentication (development only - NOT for production)
export ORCHEO_AUTH_MODE=disabled

# Optional authentication (validates tokens when provided)
export ORCHEO_AUTH_MODE=optional

# Required authentication (all requests must be authenticated)
export ORCHEO_AUTH_MODE=required
```

**Default behavior**: When `ORCHEO_AUTH_MODE` is not set, authentication defaults to `optional` but will automatically enforce authentication if any credentials are configured (JWT secrets, JWKS URLs, or service tokens).

### Recommended Settings by Environment

| Environment | Mode | Rationale |
|------------|------|-----------|
| Local Development | `optional` or `disabled` | Easier testing without credentials |
| Staging | `required` | Validate authentication before production |
| Production | `required` | Protect all endpoints and data |

## Service Token Authentication

Service tokens are ideal for CLI usage, CI/CD pipelines, and automated service integrations. They are long-lived, can be scoped to specific workspaces, and support rotation and revocation.

### Bootstrap Token for Initial Setup

When first deploying Orcheo with authentication enabled, you need a way to create your first service token without already having credentials. The **bootstrap service token** solves this "chicken and egg" problem.

#### What is a Bootstrap Token?

A bootstrap token is a special service token configured via the `ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN` environment variable. Unlike regular service tokens:

- **Not stored in the database**: Read directly from the environment
- **Full admin access by default**: Can create other tokens and manage all resources (scopes are configurable)
- **Optional expiration**: Set `ORCHEO_AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT` to enforce an automated cut-off
- **Intended for initial setup only**: Should be removed after creating persistent tokens

#### Setting Up a Bootstrap Token

1. **Generate a secure random token**:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. **Configure the environment**:
   ```bash
   export ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN="your-secure-random-token"
   export ORCHEO_AUTH_MODE=required
   ```
   Optional: set `ORCHEO_AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT` (ISO 8601 or UNIX epoch) to automatically disable the bootstrap token after a deadline, for example `2024-05-01T12:00:00Z`.

   ⚠️ **Security Note**: Bootstrap tokens should be treated as root credentials.
   Store them in secure secret management systems, not in plain text files.

3. **Start the server**:
   ```bash
   orcheo-dev-server
   ```

4. **Create your first persistent token**:
   ```bash
   export ORCHEO_SERVICE_TOKEN="your-secure-random-token"
   orcheo token create --name production-token \
     --scope workflows:read \
     --scope workflows:write \
     --scope workflows:execute
   ```

5. **Update your environment to use the persistent token**:
   ```bash
   export ORCHEO_SERVICE_TOKEN="<new-token-from-step-4>"
   unset ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN  # Remove bootstrap token
   ```

6. **Restart the server** without the bootstrap token for production use.

#### Bootstrap Token Scopes

By default, the bootstrap token grants all admin scopes. You can restrict this with `ORCHEO_AUTH_BOOTSTRAP_TOKEN_SCOPES`:

```bash
export ORCHEO_AUTH_BOOTSTRAP_TOKEN_SCOPES="admin:tokens:write,workflows:read"
```

#### Bootstrap Token Expiration

To prevent long-lived secrets, set `ORCHEO_AUTH_BOOTSTRAP_TOKEN_EXPIRES_AT` to either an ISO 8601 timestamp (`2024-05-01T12:00:00Z`) or a UNIX epoch (`1714564800`). Once the timestamp passes, Orcheo rejects the bootstrap token with `auth.token_expired` and records a failed authentication event. Renew the value before it expires if you still need the bootstrap token.

#### Security Considerations

- **Use only during initial setup**: The bootstrap token should be temporary
- **Remove after creating persistent tokens**: Don't leave it configured in production
- **Rotate if exposed**: Generate a new random token if the bootstrap token is compromised
- **Monitor usage**: Check logs for bootstrap token authentication events
- **Use secret managers**: Store the bootstrap token in AWS Secrets Manager, Vault, etc.

#### Bootstrap Token vs. Disabled Auth Mode

| Approach | Use Case | Security |
|----------|----------|----------|
| **Bootstrap Token** | Production deployments with `AUTH_MODE=required` | ✅ Secure - authentication always enforced |
| **Disabled Auth Mode** | Local development only | ⚠️ Insecure - no authentication at all |

**Recommendation**: Always use the bootstrap token approach for production deployments.

### Managing Service Tokens

All service token management is done through the CLI or API. Tokens are stored in the configured service-token repository with SHA256-hashed secrets.

#### Creating a Service Token

```bash
# Create a basic service token
orcheo token create

# Create a token with a human-readable name
orcheo token create --name my-ci-token

# Create a token with specific scopes
orcheo token create --name backend-service \
  --scope workflows:read \
  --scope workflows:execute

# Create a token with workspace access restrictions
orcheo token create --name analytics-service \
  --workspace ws-analytics \
  --workspace ws-reports

# Create a token that expires in 30 days (2,592,000 seconds)
orcheo token create --name temp-token --expires-in 2592000
```

The server assigns each token an ID; `--name` is an optional human-readable
label (it need not be unique). Use the ID from the creation output or
`orcheo token list` for `show` and `revoke`.

**Important**: The token secret is displayed only once during creation. Store it securely in a password manager or secrets vault.

Example output:
```
Service token created successfully!

ID: 6f0c1f6a-6a3e-4d5a-9b3e-2f4f9c9d1a2b
Name: my-ci-token
Secret: VGhpc0lzQW5FeGFtcGxlU2VjcmV0VG9rZW5TdHJpbmc=

⚠ Store this secret securely. It will not be shown again.

Scopes: workflows:read, workflows:execute
Workspaces: ws-analytics
Expires: 2025-12-03 15:30:00 UTC
```

#### Listing Service Tokens

```bash
# List all service tokens
orcheo token list
```

This displays a table with token IDs, scopes, workspaces, issuance dates, expiration dates, and status.

#### Viewing Token Details

```bash
# Show detailed information for a specific token (use the ID from `orcheo token list`)
orcheo token show <token-id>
```

#### Replacing Service Tokens

To replace a token, create a new one, update your services/CI systems to use it,
then revoke the old token:

```bash
# Create the replacement, then revoke the old token once it is in use
orcheo token create --name my-ci-token --scope workflows:read
orcheo token revoke <old-token-id> --reason "Replaced by new token"
```

#### Revoking Service Tokens

Immediately invalidate a token:

```bash
# Revoke a token with a reason (the --reason flag is required)
orcheo token revoke <token-id> --reason "Token compromised"
```

Revoked tokens cannot be used for authentication and the action is logged in the audit trail.

### Using Service Tokens

#### With the CLI

Store your service token in an environment variable:

```bash
export ORCHEO_SERVICE_TOKEN="your-token-secret-here"

# Now CLI commands will authenticate automatically
orcheo workflow list
orcheo workflow run my-workflow
```

Alternatively, configure it in your CLI profile at `~/.config/orcheo/cli.toml`:

```toml
[default]
api_url = "https://orcheo.example.com"
service_token = "your-token-secret-here"
```

#### With the Python SDK

```python
import os
from orcheo_sdk import HttpWorkflowExecutor, OrcheoClient

client = OrcheoClient(base_url="https://orcheo.example.com")
executor = HttpWorkflowExecutor(
    client,
    auth_token=os.environ.get("ORCHEO_SERVICE_TOKEN"),
)

result = executor.trigger_run(
    "<workflow-id>",
    workflow_version_id="<version-id>",
    triggered_by="ci",
    inputs={},
)
print(result)
```

Tip: `orcheo code scaffold <workflow_id>` generates this snippet pre-filled for
an uploaded workflow.

#### With HTTP Requests

Include the token in the `Authorization` header:

```bash
curl -H "Authorization: Bearer YOUR_SERVICE_TOKEN" \
  https://orcheo.example.com/api/workflows
```

#### In CI/CD Pipelines

**GitHub Actions**:
```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Deploy workflow
        env:
          ORCHEO_SERVICE_TOKEN: ${{ secrets.ORCHEO_SERVICE_TOKEN }}
        run: |
          orcheo workflow upload deployment.py
```

**GitLab CI**:
```yaml
deploy:
  script:
    - orcheo workflow upload deployment.py
  variables:
    ORCHEO_SERVICE_TOKEN: $ORCHEO_SERVICE_TOKEN
```

### Service Token Configuration

Service tokens are stored in the Postgres-backed repository — `postgres` is
both the default and the only accepted value:

```bash
export ORCHEO_AUTH_SERVICE_TOKEN_BACKEND=postgres  # default; other values are rejected
export ORCHEO_POSTGRES_DSN=postgresql://user:pass@host:5432/orcheo
```

## JWT Authentication

JWT authentication is designed for interactive user sessions, IdP integration, and federated authentication scenarios.

### Symmetric Key (HS256) Authentication

Use a shared secret for signing and validating tokens:

```bash
# Set a strong secret key (minimum 32 bytes recommended)
export ORCHEO_AUTH_JWT_SECRET="your-very-long-and-secure-secret-key-here"

# Restrict to HS256 algorithm only (optional, defaults to RS256,HS256)
export ORCHEO_AUTH_ALLOWED_ALGORITHMS="HS256"

# Optional: Enforce audience validation
export ORCHEO_AUTH_AUDIENCE="orcheo-api"

# Optional: Enforce issuer validation
export ORCHEO_AUTH_ISSUER="orcheo-auth-service"
```

#### Generating a Secure Secret

```bash
# Generate a random 64-byte secret
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### Asymmetric Key (RS256) Authentication with JWKS

> **Dormant path.** Production user authentication is the first-party HS256
> identity provider above. The generic JWKS relying-party layer is retained
> for a future enterprise-SSO initiative — configure it only when federating
> with an external IdP.

For deployments federating with an external Identity Provider (IdP) like Auth0, Okta, or Keycloak:

```bash
# Configure JWKS URL for fetching public keys
export ORCHEO_AUTH_JWKS_URL="https://your-idp.com/.well-known/jwks.json"

# Set cache TTL for JWKS (seconds)
export ORCHEO_AUTH_JWKS_CACHE_TTL=300

# Set timeout for JWKS fetches (seconds)
export ORCHEO_AUTH_JWKS_TIMEOUT=5.0

# Restrict to RS256 algorithm
export ORCHEO_AUTH_ALLOWED_ALGORITHMS="RS256"

# Validate audience claim
export ORCHEO_AUTH_AUDIENCE="orcheo-production"

# Validate issuer claim
export ORCHEO_AUTH_ISSUER="https://your-idp.com/"
```

### Static JWKS Configuration

For environments where dynamic JWKS fetching isn't available:

```bash
# Inline JWKS as JSON
export ORCHEO_AUTH_JWKS_STATIC='{
  "keys": [
    {
      "kty": "RSA",
      "kid": "key-id-1",
      "use": "sig",
      "alg": "RS256",
      "n": "base64-encoded-modulus",
      "e": "AQAB"
    }
  ]
}'
```

Or use `ORCHEO_AUTH_JWKS` as an alternative environment variable name.

### JWT Token Claims

Orcheo extracts identity information from JWT claims:

| Claim | Purpose |
|-------|---------|
| `sub` | Subject identifier (user ID) |
| `jti` | Token ID for tracking and revocation |
| `iat` | Issued at timestamp |
| `exp` | Expiration timestamp |
| `aud` | Audience (validated if configured) |
| `iss` | Issuer (validated if configured) |
| `scope` / `scopes` / `scp` | Space or comma-delimited scopes |
| `workspace_ids` / `workspaces` / `workspace_id` / `workspace` | Authorized workspace IDs |
| `type` / `typ` / `token_use` | Token type (user, service, client) |

Custom nested claims under `orcheo` object:
```json
{
  "sub": "user-123",
  "orcheo": {
    "scopes": ["workflows:read", "workflows:execute"],
    "workspace_ids": ["ws-production", "ws-staging"]
  }
}
```

### Using JWT Tokens

Include the JWT in the `Authorization` header:

```bash
curl -H "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..." \
  https://orcheo.example.com/api/workflows
```

With the Python SDK:
```python
client = OrcheoClient(base_url="https://orcheo.example.com")
executor = HttpWorkflowExecutor(client, auth_token=user_jwt_token)
```

## WebSocket Authentication

WebSocket connections support authentication via:

### 1. Authorization Header (Preferred)

```javascript
const ws = new WebSocket('wss://orcheo.example.com/ws/workflow/<workflow-id>', {
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN_HERE'
  }
});
```

### 2. Query Parameter

```javascript
const ws = new WebSocket(
  'wss://orcheo.example.com/ws/workflow/<workflow-id>?token=YOUR_TOKEN_HERE'
);
```

Or using `access_token`:
```javascript
const ws = new WebSocket(
  'wss://orcheo.example.com/ws/workflow/<workflow-id>?access_token=YOUR_TOKEN_HERE'
);
```

**Note**: Query parameter authentication is less secure than header-based authentication and should only be used when headers aren't supported by your WebSocket client.

## Authorization and Scopes

### Understanding Scopes

Scopes define fine-grained permissions for authenticated identities:

| Scope | Description |
|-------|-------------|
| `workflows:read` | List and view workflow definitions |
| `workflows:write` | Create, update, and delete workflows |
| `workflows:execute` | Trigger workflow executions |
| `vault:read` | Access credential vault (read-only) |
| `vault:write` | Manage credentials in vault |
| `admin:tokens:read` | View service tokens |
| `admin:tokens:write` | Create, rotate, and revoke service tokens |
| `chatkit:session` | Issue short-lived ChatKit session JWTs via `POST /api/chatkit/session` |

### Checking Scopes in Code

```python
from fastapi import Depends
from orcheo_backend.app.authentication import (
    RequestContext,
    require_scopes,
    get_request_context
)

# Require specific scopes for an endpoint
@app.get("/api/workflows")
async def list_workflows(
    context: RequestContext = Depends(require_scopes("workflows:read"))
):
    # This endpoint requires workflows:read scope
    return {"workflows": [...]}

# Or manually check scopes
@app.post("/api/workflows")
async def create_workflow(
    context: RequestContext = Depends(get_request_context)
):
    if not context.has_scope("workflows:write"):
        raise HTTPException(status_code=403, detail="Missing workflows:write scope")
    # Create workflow...
```

### Workspace-Based Authorization

Restrict token access to specific workspaces:

```python
from orcheo_backend.app.authentication import (
    require_workspace_access,
    ensure_workspace_access
)

# Using a dependency
@app.get("/api/workspaces/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    context: RequestContext = Depends(require_workspace_access("ws-production"))
):
    # Only tokens with ws-production access can call this
    return {"workspace": workspace_id}

# Manual checking
from orcheo_backend.app.authentication import get_authorization_policy

@app.get("/api/workspaces/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    policy: AuthorizationPolicy = Depends(get_authorization_policy)
):
    policy.require_workspace(workspace_id)
    # Continue with authorized access
```

## Rate Limiting

Protect against brute-force attacks with per-IP and per-identity rate limiting:

```bash
# Maximum authentication failures per IP address (0 disables)
export ORCHEO_AUTH_RATE_LIMIT_IP=10

# Maximum failures per authenticated identity (0 disables)
export ORCHEO_AUTH_RATE_LIMIT_IDENTITY=5

# Sliding window interval in seconds
export ORCHEO_AUTH_RATE_LIMIT_INTERVAL=60
```

**Example**: With `ORCHEO_AUTH_RATE_LIMIT_IP=10` and `ORCHEO_AUTH_RATE_LIMIT_INTERVAL=60`, an IP address that fails authentication 10 times within 60 seconds will be blocked with a `429 Too Many Requests` response.

### Rate Limit Responses

When rate limited, the API returns:
```json
{
  "message": "Too many authentication attempts from IP 192.0.2.1",
  "code": "auth.rate_limited.ip"
}
```

Headers include:
```
HTTP/1.1 429 Too Many Requests
Retry-After: 60
```

## Security Best Practices

### Service Token Management

1. **Use descriptive identifiers**: Name tokens by their purpose (e.g., `github-actions-deploy`, `analytics-service`)
2. **Apply least privilege**: Only grant necessary scopes and workspace access
3. **Set expiration dates**: Use `--expires-in` for temporary access
4. **Rotate regularly**: Implement 30-60 day rotation schedules for long-lived tokens
5. **Revoke immediately on compromise**: Use `orcheo token revoke` if a token is exposed
6. **Store securely**: Use password managers (1Password, LastPass) or secret managers (AWS Secrets Manager, HashiCorp Vault)
7. **Never commit to git**: Add token values to `.gitignore` and use environment variables

### JWT Configuration

1. **Use strong secrets**: Minimum 32 bytes for HS256, generated randomly
2. **HS256 is the supported production path**: the first-party IdP signs with `ORCHEO_AUTH_JWT_SECRET`; RS256/JWKS is reserved for external-IdP federation
3. **Validate claims**: Always configure `ORCHEO_AUTH_AUDIENCE` and `ORCHEO_AUTH_ISSUER`
4. **Restrict algorithms**: Use `ORCHEO_AUTH_ALLOWED_ALGORITHMS` to prevent algorithm confusion attacks
5. **Enable JWKS caching**: Reduce external dependencies and improve performance
6. **Monitor token expiration**: Implement token refresh flows for user sessions

### Network and Transport Security

1. **Always use HTTPS/TLS**: Never send tokens over unencrypted connections
2. **Configure CORS properly**: Restrict origins in production
3. **Use secure WebSocket (WSS)**: Encrypt WebSocket traffic
4. **Set proper headers**: Enable HSTS, X-Content-Type-Options, X-Frame-Options

### Operational Security

1. **Enable rate limiting**: Always configure rate limits in production
2. **Monitor authentication logs**: Track failed authentication attempts
3. **Review token usage**: Regularly audit service tokens with `orcheo token list`
4. **Implement alerting**: Set up alerts for unusual authentication patterns
5. **Document token ownership**: Maintain a registry of which tokens are used where

### Development vs. Production

| Setting | Development | Production |
|---------|------------|------------|
| `ORCHEO_AUTH_MODE` | `optional` or `disabled` | `required` |
| `ORCHEO_AUTH_JWT_SECRET` | Simple test secret | Strong 64-byte random secret |
| Rate limiting | Disabled (`0`) | Enabled (`10-20` attempts) |
| JWKS | Static test keys | Production IdP JWKS URL |
| Token expiration | Long or none | Regular rotation schedule |

## Troubleshooting

### Common Errors

#### "Missing bearer token"

**Cause**: No `Authorization` header provided or token is empty.

**Solution**:
```bash
# Ensure token is set
echo $ORCHEO_SERVICE_TOKEN

# Check API call includes Authorization header
curl -v -H "Authorization: Bearer $ORCHEO_SERVICE_TOKEN" \
  https://orcheo.example.com/api/workflows
```

#### "Invalid bearer token"

**Possible causes**:
- Token is malformed
- Token doesn't match any service token
- JWT signature validation failed
- Token has been revoked

**Solution**:
```bash
# For service tokens, verify it exists and isn't revoked
orcheo token list

# For JWTs, check:
# 1. JWT secret matches
# 2. JWKS URL is accessible
# 3. Token hasn't expired
# 4. Algorithm is allowed
```

#### "Bearer token has expired"

**Cause**: JWT `exp` claim or service token expiration date has passed.

**Solution**:
- For service tokens: Create a new token and revoke the old one
- For JWTs: Implement token refresh flow or reauthenticate

#### "Missing required scopes: workflows:write"

**Cause**: Token doesn't have the required scope.

**Solution**:
```bash
# Create a new token with the required scope
orcheo token create --name new-token --scope workflows:write
```

#### "Workspace access denied"

**Cause**: Token isn't authorized for the requested workspace.

**Solution**:
```bash
# Create token with workspace access
orcheo token create --name workspace-token \
  --workspace ws-production
```

#### "Too many authentication attempts"

**Cause**: Rate limit exceeded.

**Solution**:
- Wait for the rate limit window to expire (check `Retry-After` header)
- Fix authentication credentials to avoid repeated failures
- Contact administrator if you believe you're being blocked incorrectly

### Debugging Authentication

#### Enable Debug Logging

```bash
export ORCHEO_LOG_LEVEL=DEBUG
export ORCHEO_LOG_SENSITIVE_DEBUG=1

# Start Orcheo backend
orcheo-dev-server
```

This logs detailed authentication events, token validation steps, and scope checks.

#### Check Authentication Status

Use the request context in endpoints:
```python
@app.get("/debug/auth")
async def debug_auth(
    context: RequestContext = Depends(get_request_context)
):
    return {
        "authenticated": context.is_authenticated,
        "subject": context.subject,
        "identity_type": context.identity_type,
        "scopes": list(context.scopes),
        "workspace_ids": list(context.workspace_ids),
        "token_id": context.token_id
    }
```

#### Verify JWKS Configuration

```bash
# Test JWKS URL is accessible
curl $ORCHEO_AUTH_JWKS_URL

# Should return:
# {
#   "keys": [...]
# }
```

#### Test Token Locally

```python
import jwt

token = "your-jwt-token"
secret = "your-secret-key"

try:
    claims = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience="orcheo-api"
    )
    print("Token valid:", claims)
except jwt.ExpiredSignatureError:
    print("Token expired")
except jwt.InvalidTokenError as e:
    print("Token invalid:", e)
```

### Getting Help

If you continue to experience authentication issues:

1. Check the [environment variables documentation](environment_variables.md)
2. Review server logs for detailed error messages
3. Verify your configuration matches the authentication mode
4. Consult the [authentication design document](../project/architecture/authentication_design.md) for architecture details
5. Open an issue on GitHub with logs and configuration details (redact secrets!)

## Related Documentation

- [Environment Variables Reference](environment_variables.md) - Complete list of authentication-related environment variables
- [Authentication Design](../project/architecture/authentication_design.md) - Architecture and design decisions
- [Deployment Guide](deployment.md) - Production deployment considerations
- [Security Review](../project/milestones/milestone3_security_review.md) - Security audit findings and mitigations
