# Design Document

## For Bundled Caddy Ingress for Standard Self-Hosted Installs

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-04-09
- **Status:** Approved

---

## Overview

This design adds Caddy to the standard Orcheo stack as an optional ingress tier for public self-hosted installs. The target environment is a reachable self-hosted Linux host, including cloud VMs and on-premise servers running Docker, which matches the Linux-based Orcheo stack images, where the operator controls DNS and inbound ports. Caddy terminates HTTPS for the public hostname and routes browser traffic to Studio and backend services already present in the stack.

The design intentionally does not try to replace Cloudflare Tunnel. Tunnel products solve public exposure for machines that are not directly reachable from the internet. Bundled Caddy instead standardizes the conventional deployment path for reachable self-hosted infrastructure. The outcome is a simpler, better-documented public install path without expanding Orcheo into a relay-network product.

## Components

- **Caddy Ingress (`deploy/stack/Caddyfile`, `deploy/stack/docker-compose.yml`)**
  - Terminates TLS for the configured public hostname.
  - Redirects HTTP to HTTPS.
  - Routes Studio SPA traffic, backend API traffic, and backend WebSockets.
  - Key dependencies: public DNS, open inbound `80/443`, container network reachability to backend and Studio.

- **Stack Setup Orchestrator (`packages/sdk/src/orcheo_sdk/cli/setup.py`)**
  - Adds a public-ingress installation mode on top of the existing local stack flow.
  - Prompts for or accepts flags for hostname and public-ingress enablement.
  - Writes the env/config values required for Studio, backend, and Caddy to agree on the public origin.

- **Studio Runtime Configuration (`apps/studio/src/lib/config.ts`, stack env injection)**
  - Uses the configured public backend URL for API and WebSocket traffic.
  - Must accept the public hostname when served behind the ingress host.
  - Key dependencies: `VITE_ORCHEO_BACKEND_URL`, `VITE_ORCHEO_ALLOWED_HOSTS`.

- **Backend Runtime Configuration (`apps/backend/`, stack env)**
  - Continues serving API and WebSocket routes internally.
  - Uses public-origin settings for CORS and ChatKit/share-link generation.
  - Key dependencies: `ORCHEO_CORS_ALLOW_ORIGINS`, `ORCHEO_CHATKIT_PUBLIC_BASE_URL`.

- **Operator Networking**
  - Owns DNS records, firewall rules, and host/public-IP exposure.
  - Remains outside Orcheo’s automation boundary.

## Request Flows

### Flow 1: Public self-hosted installation with bundled ingress

1. Operator runs `orcheo install`.
2. Setup asks whether the stack is local-only or publicly reachable.
3. If public ingress is enabled, setup prompts for the public hostname and writes ingress-related env/config.
4. Stack assets include Caddy plus the existing backend, Studio, worker, beat, Postgres, and Redis services.
5. Operator points DNS for the hostname to the host and ensures inbound `80/443` reach the Caddy host.
6. Caddy obtains and renews certificates automatically for the configured hostname.
7. Browser traffic reaches Orcheo through the public hostname over HTTPS.

### Flow 2: Public browser request

1. User opens `https://orcheo.example.com/`.
2. Caddy accepts the HTTPS connection and serves Studio for `/`.
3. Studio calls `https://orcheo.example.com/api/...` for backend APIs.
4. Caddy proxies `/api/*` to backend.
5. Studio opens `wss://orcheo.example.com/ws/workflow/...` for workflow execution streams.
6. Caddy proxies `/ws/*` to backend, preserving WebSocket behavior.

### Flow 3: Multiple backend replicas of one deployment

1. Operator runs several backend replicas that share the same Postgres and Redis services.
2. Caddy is configured with multiple backend upstreams for `/api/*` and `/ws/*`.
3. Caddy applies health checks and load-balancing policy across those upstreams.
4. Requests are distributed across replicas of the same logical deployment.

Constraint:
- This flow is only valid when all upstreams are replicas of the same deployment. It is not a valid routing model for isolated customer-specific stacks.

## API Contracts

### Public ingress routing contract

```
GET https://<public-host>/
Response:
  200 OK -> Studio SPA

GET https://<public-host>/api/system/info
Response:
  200 OK -> backend API response

GET/POST wss://<public-host>/ws/workflow/{workflow_ref}
Response:
  101 Switching Protocols -> backend WebSocket session
```

### Generated stack configuration contract

Setup must be able to derive and write a consistent public-origin configuration set:

```env
ORCHEO_PUBLIC_INGRESS_ENABLED=true
ORCHEO_PUBLIC_HOST=orcheo.example.com
ORCHEO_CORS_ALLOW_ORIGINS=https://orcheo.example.com
ORCHEO_CHATKIT_PUBLIC_BASE_URL=https://orcheo.example.com
VITE_ORCHEO_BACKEND_URL=https://orcheo.example.com
VITE_ORCHEO_ALLOWED_HOSTS=orcheo.example.com
```

Field names above are illustrative for the contract. The final implementation may consolidate them into existing env vars instead of adding all new names.

## Data Models / Schemas

### Bundled ingress mode

| Field | Type | Description |
|-------|------|-------------|
| enabled | boolean | Whether Caddy is included as the public ingress tier |
| public_host | string | Public DNS hostname served by Caddy |
| publish_debug_ports | boolean | Whether backend/Studio localhost ports remain published alongside Caddy |
| backend_upstreams | list[string] | Internal backend upstreams used by Caddy |
| studio_upstream | string | Internal Studio upstream used by Caddy |

### Caddy route model

```json
{
  "routes": [
    { "match": "/", "upstream": "Studio" },
    { "match": "/api/*", "upstream": "backend" },
    { "match": "/ws/*", "upstream": "backend" }
  ]
}
```

## Security Considerations

- Caddy terminates TLS, but it does not remove the need for Orcheo auth. Existing backend auth modes remain in force.
- The public hostname must be the canonical browser origin for CORS and OAuth-related configuration.
- Backend and Studio should not be publicly exposed on separate raw ports by default in public-ingress mode; only Caddy should need public exposure.
- Forwarded headers and client IP handling must be documented carefully if operators place another load balancer in front of Caddy.
- This design does not introduce WAF, bot management, DDoS shielding, or CDN semantics. Operators who require those controls still need upstream infrastructure.
- Certificates and any Caddy state must be stored persistently if the ingress container is recreated.

## Performance Considerations

- Bundled Caddy is appropriate for standard self-hosted installs and moderate scale.
- It is not the primary scale bottleneck for most Orcheo installs; Postgres, Redis, worker capacity, and backend replica count are more likely to constrain throughput first.
- Caddy supports multiple upstreams, retries, health checks, and load balancing for replicas of the same deployment.
- Long-lived WebSocket sessions remain anchored to the backend replica that accepted them after upgrade.
- For very high scale or stricter edge requirements, operators should use a cloud-managed LB or ingress tier in front of or instead of bundled Caddy.

## Testing Strategy

- **Unit tests**
  - Setup/env generation for public-ingress mode.
  - Validation of hostname-related stack config.
  - Caddy config generation helpers if generated dynamically.

- **Integration tests**
  - Compose-level ingress smoke test:
    - `/` serves Studio
    - `/api/system/info` reaches backend
    - `/ws/workflow/...` upgrades and proxies correctly
  - Replica-mode test with multiple backend upstreams sharing one repository and broker.

- **Manual QA checklist**
  - Single-host public deployment on a reachable self-hosted host with real DNS and HTTPS.
  - Public-origin Studio login and API usage.
  - WebSocket-driven workflow execution through the public hostname.
  - Failure messaging when DNS or inbound ports are not configured correctly.

## Rollout Plan

1. Phase 1: Add bundled Caddy assets and internal dogfood on a reachable self-hosted host.
2. Phase 2: Extend setup flow and docs for public self-hosted mode.
3. Phase 3: Validate multi-replica backend routing for one logical deployment.
4. Phase 4: Document advanced scale-out guidance and boundaries of the bundled pattern.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-04-09 | Codex | Initial draft |
