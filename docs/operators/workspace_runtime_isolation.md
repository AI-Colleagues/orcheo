# Workspace Runtime Isolation — Operator Guide

This guide tells operators how to deploy, observe, and roll out workspace
runtime isolation. Sandboxing is **always on** in the deployed stack —
every vibe agent session and tenant-authored workflow run executes inside a
per-workspace gVisor sandbox. The root `docker-compose.yml` keeps sandboxing
disabled by default for local development; the sandbox infrastructure still
starts, but the backend and worker only use it when
`ORCHEO_SANDBOX_DISABLED=false`.

For background, see the initiative documents under
`project/initiatives/workspace_runtime_isolation/`.

## Prerequisites

1. A Linux host with the `runsc` (gVisor) Docker runtime registered. On
   standard EC2 (no `/dev/kvm`) gVisor's `systrap` platform is required.
2. `nftables` available on the host so the L3/L4 deny ruleset can be loaded.
3. The workspace-sandbox image must be available to the Docker daemon that
   `sandbox-runtime` talks to. The deploy stack defaults
   `ORCHEO_SANDBOX_IMAGE` to
   `ghcr.io/ai-colleagues/orcheo-workspace-sandbox:latest`, which CI
   publishes on `stack-v*` tags alongside the other stack images;
   `sandbox-runtime` pulls it on first use. For local development the root
   `docker compose up -d` brings the sandbox services up, but the backend and
   worker ignore them unless `ORCHEO_SANDBOX_DISABLED=false`. Rebuild the
   image with `make workspace-sandbox-build` when you change
   `Dockerfile.workspace-sandbox`. You can also push your own tagged build to
   a registry the host can pull from and point `ORCHEO_SANDBOX_IMAGE` at it.
   One sandbox image hosts both vibe agent sessions and tenant workflow runs
   per workspace.

## Configuration

Sandbox runtime configuration lives in `SandboxSettings` and is read by the
backend / worker through `AppSettings`. The env vars operators typically
override:

| Variable                              | Default                                                                | Purpose                                                  |
|---------------------------------------|------------------------------------------------------------------------|----------------------------------------------------------|
| `ORCHEO_CONTAINER_RUNTIME`            | `runsc`                                                                | Docker runtime name (`runsc` for gVisor).                |
| `ORCHEO_SANDBOX_IMAGE`                | `ghcr.io/ai-colleagues/orcheo-workspace-sandbox:latest`                | Image hosting agent CLIs, Orcheo CLI, and workflow runner. Pulled from GHCR in prod; built locally by the dev compose. |
| `ORCHEO_SANDBOX_RUNTIME_URL`          | `http://sandbox-runtime:9090`                                          | Internal URL of the sandbox-runtime service. Backend and worker call this to provision sandboxes and dispatch runs — they never mount the Docker socket themselves. |
| `ORCHEO_SANDBOX_CONTROL_TOKEN`        | _(required)_                                                           | Internal authentication token sent by backend/worker to `sandbox-runtime`; do not expose it to child containers or the relay. |
| `ORCHEO_EGRESS_PROXY_URL`             | `http://egress-proxy:3128`                                             | Sole external HTTP/HTTPS path for child sandboxes.         |
| `ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS` | _(empty; deny all external hosts)_                                      | Comma-separated global hostname allowlist used to render the Envoy proxy policy. Non-HTTP egress is unsupported. |
| `ORCHEO_CREDENTIAL_BROKER_URL`        | `http://credential-relay:9091/credentials/resolve`                    | Only internal child-reachable credential endpoint. The relay has no lifecycle or exec operations. |
| `ORCHEO_CREDENTIAL_BROKER_FORWARD_URL`| `http://backend:2025/internal/credentials/resolve`                     | Upstream broker URL read only by `credential-relay` on the control network. |
| `ORCHEO_SANDBOX_DNS`                  | _(unset)_                                                              | Compatibility override only. Hardened sandbox deployments do not inject public DNS; relay/proxy hostnames are pinned into `/etc/hosts`, and the proxy resolves external hosts. |
| `ORCHEO_CREDENTIAL_BROKER_SECRET`     | _(required — backend refuses to start if unset)_                       | HMAC secret for run-scoped tokens — generate with `python -m orcheo.sandbox.broker --gen-secret`. |
| `ORCHEO_SANDBOX_DISABLED`             | `false` unless an environment sets it to `true` (the root `docker-compose.yml` does so for local development) | Boolean | Disables sandbox runtime dispatch entirely. Keep this `false` in any deployment that should preserve workspace isolation. |
| `ORCHEO_SANDBOX_FAST_PATH_TRUSTED`    | `false`                                                                | When `true`, workflows composed only of trusted node types skip the sandbox (workflow runs only — vibe agents always sandbox). |

## Deploy

```
docker compose up -d           # starts the local stack with sandboxing disabled
nft -f deploy/stack/sandbox-egress.nft
```

The `sandbox-runtime`, `credential-relay`, `egress-proxy`, and
`workspace-sandbox` services are defined in the base `docker-compose.yml`.
`sandbox-runtime` is attached only to the control network and is the only
service mounting the Docker socket. The relay and proxy are assigned fixed
addresses on `sandbox-egress`; tenant addresses are allocated from
`10.99.0.128/25`, which is the source range matched by `sandbox-egress.nft`.

The `workspace-sandbox` image is what the `sandbox-runtime` service spawns
on demand to host vibe-agent sessions and tenant workflow runs — it is *not*
a long-lived service. It's declared as a normal Compose service so
`docker compose up -d` builds the image automatically; its `command:` is a
no-op `exit 0`, so the resulting container exits immediately and the image
is then consumed only by `sandbox-runtime` over the Docker API. After
editing `Dockerfile.workspace-sandbox`, rebuild with `make
workspace-sandbox-build` (or `docker compose build workspace-sandbox`).
Production deployments typically push a tagged version of this image to an
internal registry and override `ORCHEO_SANDBOX_IMAGE` instead.

The checked Envoy config denies all external hosts. Materialize an
operator-managed global hostname allowlist into the mounted config before
starting production egress:

```python
from orcheo.sandbox.egress.proxy import EnvoyForwardProxyConfig

config = EnvoyForwardProxyConfig.from_env()
print(config.render_yaml())
```

Set `ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS` in the environment running that
render step and mount the resulting file through `ORCHEO_EGRESS_PROXY_CONFIG`.
There is no per-workspace exception path: a host is either globally approved
or denied and logged.

## Observability

| Signal                                       | Where                                       |
|----------------------------------------------|---------------------------------------------|
| Sandbox lifecycle events                     | `orcheo.sandbox.audit` log channel.         |
| Denied egress hosts                          | `egress-proxy` audit log + audit consumer.  |
| Per-sandbox counters                         | `InMemoryMetricsRecorder.snapshot()` (Prometheus exporter coming later). |
| Idle reaping                                 | `SandboxRuntimeManager.reap_idle()` on a 60s cron tick. |

The recommended dashboard layout is:

1. Lifecycle: provisions / destroys per minute, per workspace.
2. Egress: denied requests per minute, top denied hosts.
3. Pool utilization: warm-pool size vs concurrent in-use sandboxes.
4. Latency: agent-session start, workflow-run cold-start vs warm-start.

## Rollout

Sandboxing is always on in the deployed stack. The phased rollout that
originally gated the feature ended on 2026-05-19 when the Milestone 0 gVisor
compatibility spike was signed off. Self-hosted deployments inherit the same
boundary; tune warm-pool sizing per workspace in `WorkspaceRuntimePool` if
cold-start latency becomes visible.

## Troubleshooting

- **Agent session start fails immediately.** Check the audit log for a
  `provision` event followed by a `destroy`. Typical causes: image pull
  failure, `runsc` not registered, or scratch tmpfs size too small for the
  agent's working tree.
- **Workflow run reports `forbidden host`.** The egress proxy denied a host
  not on the global operator allowlist. Update
  `ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS` and regenerate the Envoy config.
- **Credential resolves with status 403.** The Credential Broker token's
  `workspace_id` does not match the workspace claimed by the resolver call.
  This is by design — the broker pins the workspace server-side. Verify the
  sandbox is launched with the broker token issued for the same run.
