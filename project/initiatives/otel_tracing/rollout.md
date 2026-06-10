# Trace Tab Release Notes & Rollout Checklist

This document captures the release messaging and operational steps required to enable
the Workflow Trace tab across environments.

## Release notes

- **Workflow insights at a glance** – Every workflow execution now emits an
  OpenTelemetry trace. Studio visualizes the trace as a collapsible tree so teams can
  inspect prompts, responses, token usage, latency, and artifacts without leaving the
  app.
- **Live updates** – The Trace tab streams node progress in real time for in-flight runs.
  Completed runs load historical traces on demand, matching the `/executions/{id}/trace`
  API response.
- **Observability integrations** – Deployments can forward spans to any OTLP-compatible
  backend (Tempo, Jaeger, Honeycomb, etc.). External dashboards appear in Studio when
  configured.
- **Configurable privacy controls** – Administrators can disable tracing entirely or tune
  redaction limits via environment variables described in
  [configuration.md](configuration.md).

Share these highlights in changelog or release announcements to set expectations for
stakeholders.

## Rollout checklist

### Staging

1. **Upgrade dependencies** – Deploy backend image with
   `opentelemetry-exporter-otlp` installed and ensure Studio includes the Trace tab UI.
2. **Configure tracing** – Set `ORCHEO_TRACING_EXPORTER=otlp` and point
   `ORCHEO_TRACING_ENDPOINT` at the staging collector. Use a reduced sample ratio (e.g.,
   `0.25`). Verify backend startup logs show successful tracer provider initialization.
3. **Provision collector** – Apply the sample collector configuration (see
   [configuration.md](configuration.md)) or reuse the shared observability cluster. Verify
   spans arrive in the backend by inspecting the collector UI.
4. **Run smoke tests** – Execute representative workflows via Studio and the CLI. Confirm
   traces populate in the Trace tab and external dashboards. Capture screenshots for the
   release notes.
5. **Validate retention** – Ensure trace metadata is purged according to staging policy
   and that sensitive content remains redacted.

### Production

1. **Communicate rollout window** – Notify stakeholders about the Trace tab deployment
   and any expected downtime.
2. **Promote artifacts** – Roll out the backend and Studio builds validated in staging.
3. **Update configuration** – Set production-specific values for exporter endpoint,
   service name, and sampling rate. Confirm TLS certificates or `ORCHEO_TRACING_INSECURE`
   overrides as needed.
4. **Monitor rollout** – During the first 24 hours, monitor collector load, span ingestion
   rate, token usage events, Studio performance, and any OTLP export errors in backend
   logs. Adjust sampling if ingestion volume exceeds limits.
5. **Enable external links** – If external observability dashboards are available, add the
   URLs to the Studio configuration so users can pivot directly from the Trace tab.
6. **Document completion** – Mark the Phase 4 tasks in [plan.md](plan.md) as complete and
   update public release notes with screenshots and usage docs.

Following this checklist ensures the Trace tab launches smoothly while keeping operators
and end users informed.
