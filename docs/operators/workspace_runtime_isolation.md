# Workspace Runtime Isolation — Operator Guide

This guide tells operators how to deploy, observe, and roll out workspace
runtime isolation. Sandboxing is **always on** as of Milestone 0 sign-off —
every vibe agent session and tenant-authored workflow run executes inside a
per-workspace gVisor sandbox. Self-hosted deployments still get the same
boundary; the cost is a small per-run overhead amortized by the warm pool.

For background, see the initiative documents under
`project/initiatives/workspace_runtime_isolation/`.

## Prerequisites

1. A Linux host with the `runsc` (gVisor) Docker runtime registered. On
   standard EC2 (no `/dev/kvm`) gVisor's `systrap` platform is required.
2. `nftables` available on the host so the L3/L4 deny ruleset can be loaded.
3. The `orcheo/workspace-sandbox:latest` image must be available to the
   Docker daemon that `sandbox-runtime` talks to. Either build it locally
   (`make workspace-sandbox-build` — see the Deploy section) or push a
   tagged version to a registry the host can pull from and point
   `ORCHEO_SANDBOX_IMAGE` at it. One sandbox image hosts both vibe agent
   sessions and tenant workflow runs per workspace.

## Configuration

Sandbox runtime configuration lives in `SandboxSettings` and is read by the
backend / worker through `AppSettings`. The env vars operators typically
override:

| Variable                              | Default                                                                | Purpose                                                  |
|---------------------------------------|------------------------------------------------------------------------|----------------------------------------------------------|
| `ORCHEO_CONTAINER_RUNTIME`            | `runsc`                                                                | Docker runtime name (`runsc` for gVisor).                |
| `ORCHEO_SANDBOX_IMAGE`                | `orcheo/workspace-sandbox:latest`                                      | Image hosting agent CLIs, Orcheo CLI, and workflow runner. |
| `ORCHEO_SANDBOX_RUNTIME_URL`          | `http://sandbox-runtime:9090`                                          | Internal URL of the sandbox-runtime service. Backend and worker call this to provision sandboxes and dispatch runs — they never mount the Docker socket themselves. |
| `ORCHEO_EGRESS_PROXY_URL`             | `http://egress-proxy:3128`                                             | Envoy forward proxy for permitted HTTP/HTTPS.            |
| `ORCHEO_CREDENTIAL_BROKER_URL`        | `http://sandbox-runtime:9090/credentials/resolve`                      | Endpoint workspace sandboxes use to resolve run-scoped credentials. The `sandbox-runtime` service overrides this to the backend broker upstream. |
| `ORCHEO_CREDENTIAL_BROKER_SECRET`     | _(required — backend refuses to start if unset)_                       | HMAC secret for run-scoped tokens — generate with `python -m orcheo.sandbox.broker --gen-secret`. |
| `ORCHEO_SANDBOX_FAST_PATH_TRUSTED`    | `false`                                                                | When `true`, workflows composed only of trusted node types skip the sandbox (workflow runs only — vibe agents always sandbox). |

## Deploy

```
make workspace-sandbox-build   # one-shot bootstrap of the per-workspace image
docker compose up -d
nft -f deploy/stack/sandbox-egress.nft
```

The `sandbox-runtime` and `egress-proxy` services are baked into the base
`docker-compose.yml`, so no overlay is needed.

The `workspace-sandbox` image is what the `sandbox-runtime` service spawns
on demand to host vibe-agent sessions and tenant workflow runs — it is *not*
a long-lived service. It ships as a build-only compose service under the
`build-only` profile, which is why a plain `docker compose up -d` ignores
it and the bootstrap step above (or the equivalent
`docker compose --profile build-only build workspace-sandbox`) is required
once after cloning and again whenever `Dockerfile.workspace-sandbox` changes.
Production deployments typically push a tagged version of this image to an
internal registry and override `ORCHEO_SANDBOX_IMAGE` instead.

The Envoy config at `deploy/stack/envoy-forward-proxy.yaml` can be
regenerated from per-workspace allowlists with:

```python
from orcheo.sandbox.egress import EnvoyForwardProxyConfig
from orcheo.sandbox.egress.proxy import WorkspaceEgressAllowlist

config = EnvoyForwardProxyConfig(
    workspaces=(
        WorkspaceEgressAllowlist(workspace_id="acme", hosts=("api.acme.com",)),
    ),
    global_allowed_hosts=("api.openai.com", "api.anthropic.com"),
)
print(config.render_yaml())
```

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

Sandboxing is always on. The phased rollout that originally gated the
feature ended on 2026-05-19 when the Milestone 0 gVisor compatibility spike
was signed off. Self-hosted deployments inherit the same boundary; tune
warm-pool sizing per workspace in `WorkspaceRuntimePool` if cold-start
latency becomes visible.

## Troubleshooting

- **Agent session start fails immediately.** Check the audit log for a
  `provision` event followed by a `destroy`. Typical causes: image pull
  failure, `runsc` not registered, or scratch tmpfs size too small for the
  agent's working tree.
- **Workflow run reports `forbidden host`.** The egress proxy denied a host
  not on the workspace's allowlist. Add it via the workspace egress allowlist
  (see Configuration) and regenerate the Envoy config.
- **Credential resolves with status 403.** The Credential Broker token's
  `workspace_id` does not match the workspace claimed by the resolver call.
  This is by design — the broker pins the workspace server-side. Verify the
  sandbox is launched with the broker token issued for the same run.
