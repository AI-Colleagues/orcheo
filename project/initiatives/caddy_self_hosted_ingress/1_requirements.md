# Requirements Document

## METADATA
- **Authors:** Codex
- **Project/Feature Name:** Bundled Caddy Ingress for Standard Self-Hosted Installs
- **Type:** Enhancement
- **Summary:** Bundle Caddy into the standard Orcheo stack so self-hosted operators can expose Canvas and Backend on a public HTTPS domain without requiring Cloudflare Tunnel.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-04-09

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Design Doc | `./2_design.md` | Shaojie Jiang | Caddy Self-Hosted Ingress Design |
| Project Plan | `./3_plan.md` | Shaojie Jiang | Caddy Self-Hosted Ingress Plan |
| Current Stack Install Flow | `project/initiatives/stack_installation/1_requirements.md` | Shaojie Jiang | Stack Installation Simplification and Version Awareness |
| Manual Setup Guide | `docs/manual_setup.md` | Shaojie Jiang | Manual Setup |
| Deployment Guide | `docs/deployment.md` | Shaojie Jiang | Deployment Recipes |
| Environment Variables | `docs/environment_variables.md` | Shaojie Jiang | Environment Variables |
| Stack Assets | `deploy/stack/` | Shaojie Jiang | Bundled stack assets |
| Canvas Host Validation | `apps/canvas/vite.config.ts` | Shaojie Jiang | Canvas Vite configuration |
| Backend WebSocket Router | `apps/backend/src/orcheo_backend/app/routers/websocket.py` | Shaojie Jiang | Workflow WebSocket endpoint |
| Caddy Automatic HTTPS | `https://caddyserver.com/docs/quick-starts/https` | Caddy | Official docs |
| Caddy Reverse Proxy | `https://caddyserver.com/docs/caddyfile/directives/reverse_proxy` | Caddy | Official docs |

## PROBLEM DEFINITION
### Objectives
Make public self-hosted Orcheo installs a first-class path by bundling a standard HTTPS reverse proxy with the stack. Reduce the need for operators to assemble an external reverse-proxy layer or depend on Cloudflare Tunnel when running on reachable self-hosted infrastructure, including cloud VMs and on-premise Linux servers running Docker, which matches the Linux-based Orcheo stack images.

### Target users
- Operators deploying Orcheo on reachable self-hosted Linux hosts, including cloud VMs and on-premise servers running Docker, which matches the Linux-based Orcheo stack images.
- Teams who want a standard self-hosted install path with a custom domain and HTTPS.
- Operators who need one public origin for Canvas, backend APIs, and workflow WebSockets.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Self-hosted operator | enable public HTTPS ingress during `orcheo install` | I can expose Orcheo on my own domain without separately wiring Nginx or Cloudflare Tunnel | P0 | Stack install supports a bundled Caddy ingress mode with actionable prompts and docs |
| Self-hosted operator | serve Canvas, `/api/*`, and `/ws/*` from one public origin | I can avoid ad-hoc proxy config and reduce CORS complexity | P0 | Caddy routes browser traffic correctly for Canvas, API, and workflow WebSockets |
| Self-hosted operator | configure a public hostname and allow automatic TLS | I can run a secure production-like install on a reachable host | P0 | Docs and setup clearly state DNS, open ports, and hostname requirements |
| Self-hosted operator | run multiple backend replicas behind one hostname | I can scale a single logical Orcheo deployment without replacing the ingress tier immediately | P1 | Design and docs support load balancing replicas of the same deployment |
| Developer | keep using localhost or a tunnel when needed | I do not lose the existing local-dev path for callback-driven integrations | P0 | Local-only setup remains available and Caddy ingress is optional |

### Context, Problems, Opportunities
The current bundled stack is optimized for local startup and publishes backend and Canvas directly on localhost ports. That is sufficient for development, but public self-hosting currently requires operators to add their own reverse proxy or to rely on Cloudflare Tunnel as an external workaround. This creates avoidable setup complexity, especially when users run Orcheo on reachable self-hosted infrastructure where a conventional HTTPS reverse proxy is the correct deployment primitive.

Bundling Caddy gives Orcheo a standard self-hosted ingress path that is simpler than requiring operators to assemble Nginx manually. At the same time, Cloudflare Tunnel solves a different problem: exposing non-public networks without inbound ports. The bundled Caddy path should complement, not replace, the existing local and tunnel-based workflows.

### Product goals and Non-goals
Goals:
- Provide a bundled ingress option for standard self-hosted installs on reachable hosts.
- Serve Canvas, backend APIs, and workflow WebSockets under one HTTPS origin.
- Make DNS, port, and hostname requirements explicit during setup and in docs.
- Keep the current local-only stack path intact.
- Define a scale-up path for multiple replicas of one logical deployment behind Caddy.

Non-goals:
- Replacing Cloudflare Tunnel for localhost or NAT-restricted development environments.
- Building Orcheo-managed public relay/tunnel infrastructure.
- Treating one hostname and one path as a router for multiple isolated customer stacks.
- Positioning bundled Caddy as the final answer for hyperscale internet edge traffic, WAF, or CDN needs.

## PRODUCT DEFINITION
### Requirements
**P0 (must have)**
- Add an optional bundled Caddy ingress mode to the standard stack assets under `deploy/stack/`.
- In bundled-ingress mode, Caddy is the only service that needs public `80/443` exposure. Backend and Canvas remain private to the Docker network by default.
- Caddy must route:
  - `/` and SPA paths to Canvas
  - `/api/*` to backend HTTP routes
  - `/ws/*` to backend WebSocket routes
- Setup must support a public self-hosted mode in addition to the existing local-only mode.
- Setup must collect or accept non-interactively:
  - public hostname
  - whether public ingress is enabled
  - whether local backend/canvas ports remain published for debugging
- Setup and docs must state that successful public ingress requires:
  - a DNS record pointing the chosen hostname to the host running Caddy
  - inbound `80` and `443` access to the Caddy host
  - a publicly reachable machine or equivalent private-network routing
- Stack configuration must set Orcheo’s public-origin settings correctly for bundled ingress, including:
  - Canvas backend base URL
  - backend CORS allow-origins
  - ChatKit public base URL where relevant
  - Canvas allowed hosts where relevant
- The bundled path must preserve the current localhost-friendly installation flow for users who do not enable public ingress.
- Docs must explicitly state that bundled Caddy is for standard self-hosted installs on reachable hosts and is not a replacement for Cloudflare Tunnel when inbound ports are unavailable.

**P1 (nice to have)**
- Support multiple backend replicas of the same logical Orcheo deployment behind one Caddy ingress.
- Support dynamic upstream discovery or generated multi-upstream configuration for replica pools.
- Add an explicit advanced deployment guide for putting cloud-managed LBs or ingress controllers in front of Caddy or in place of Caddy.
- Add optional Caddy admin/config reload workflows for operational updates without full stack restarts.

### Designs (if applicable)
See `./2_design.md`.

### Other Teams Impacted
- SDK/CLI: setup prompts, stack asset sync, env generation, install summaries.
- Backend: trusted public-origin config, forwarded-header behavior, CORS documentation.
- Canvas: allowed-host behavior and public backend URL defaults.
- Docs/Developer Relations: self-hosted deployment guidance and tunnel-vs-ingress positioning.

## TECHNICAL CONSIDERATIONS
### Architecture Overview
Bundle Caddy as the public entrypoint for standard self-hosted stacks while keeping backend, Canvas, Redis, Postgres, worker, and beat in the existing Compose-based topology. Caddy terminates TLS, serves the public hostname, and reverse-proxies HTTP and WebSocket traffic to the internal services.

### Technical Requirements
- Add Caddy config and container wiring to the stack asset bundle.
- Default public routing model:
  - `https://<host>/` -> Canvas
  - `https://<host>/api/...` -> backend
  - `wss://<host>/ws/...` -> backend
- Ensure the generated runtime configuration keeps Canvas talking to the public backend origin and backend CORS aligned to that origin.
- Preserve support for the workflow WebSocket endpoint already exposed by backend.
- Avoid assuming path-prefix multi-workspace for isolated stacks; same hostname + same path load balancing is only valid for replicas of one logical deployment with shared Postgres and Redis.
- Document the requirement that operators manage DNS and open inbound ports separately from Orcheo setup.
- Keep the solution compatible with standard self-hosted Linux environments, including public cloud VMs and on-premise servers running Docker, which matches the Linux-based Orcheo stack images.

### AI/ML Considerations (if applicable)
Not applicable.

## MARKET DEFINITION (for products or large features)
Not applicable; this is a self-hosting operability enhancement.

## LAUNCH/ROLLOUT PLAN
### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Public self-hosted install completion rate | >=90% of operators can reach a healthy HTTPS Orcheo stack after following the bundled Caddy path on a reachable host |
| [Secondary] Reverse-proxy setup time | Reduce setup time for standard self-hosted ingress compared with manual external proxy assembly |
| [Secondary] Support clarity | Decrease ambiguity between “reachable self-hosted host” and “localhost behind NAT” in setup and deployment docs |
| [Guardrail] Local install regressions | 0 regressions to the existing local-only install path |

### Rollout Strategy
- Ship the initiative as an optional bundled ingress mode first.
- Validate on internal reachable self-hosted installs before documenting as the default public self-hosted path.
- Keep local-only setup as the default path when the user does not opt into public ingress.

### Estimated Launch Phases
| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Internal dogfood | Add bundled Caddy assets and validate a single-host public deployment |
| **Phase 2** | Self-hosted docs | Publish self-hosted ingress docs and setup guidance |
| **Phase 3** | Replica support | Validate multiple backend replicas of one deployment behind the same ingress |

## HYPOTHESIS & RISKS
Hypothesis:
- A bundled Caddy ingress tier will make standard self-hosted Orcheo deployments materially easier without forcing Orcheo to own tunnel infrastructure.

Risks:
- Operators may confuse DNS setup with network reachability and expect Caddy to work without open inbound ports.
- Users may incorrectly treat same-hostname load balancing as a way to multiplex isolated stacks.
- Caddy may be overinterpreted as a hyperscale edge solution rather than a standard self-hosted ingress layer.

Risk Mitigation:
- Make DNS, firewall, and port-forwarding prerequisites explicit in setup and docs.
- State clearly that same-hostname same-path load balancing is only for replicas of one logical deployment.
- Document escalation paths for higher scale or stricter edge requirements, including managed cloud load balancers.

## APPENDIX
- Existing local stack bootstrap path: `orcheo install --start-stack`
- Existing stack assets: `deploy/stack/`
