# Workflow Trace Tab Implementation Plan

## Phase 1 – Backend Instrumentation & Persistence
- [x] Add OpenTelemetry dependencies to backend and shared packages; configure tracer provider with environment-driven exporter settings.
- [x] Instrument workflow execution lifecycle to create root and child spans, capturing prompts, responses, token metrics, and artifact references.
- [x] Extend run history models and repositories to store trace IDs and timestamps; add migrations if necessary.
- [x] Write unit tests covering span creation helpers and trace metadata persistence.

## Phase 2 – Trace Retrieval API & Realtime Updates
- [x] Implement `/executions/{execution_id}/trace` endpoint returning trace hierarchy, metrics, and artifact metadata.
- [x] Update serializers and schemas to expose trace data, ensuring compatibility with existing execution DTOs.
- [x] Enhance WebSocket or polling channels to deliver incremental span updates for active executions.
- [x] Add integration tests that simulate workflow runs and validate API responses and realtime payloads.

## Phase 3 – Studio Trace Tab UI
- [x] Introduce a `Trace` tab in workflow page layout, updating tab navigation and default selection logic.
- [x] Create data-fetch hooks/services that call the new trace endpoint and subscribe to realtime updates.
- [x] Build trace viewer components (tree view, details panel, metrics summary, artifact download controls).
- [x] Write frontend tests (Vitest + React Testing Library) for tab rendering, data loading, and interaction states.

## Phase 4 – Configuration, Documentation, & QA
- [x] Document OpenTelemetry configuration, deployment considerations, and Trace tab usage in docs.
- [x] Provide sample collector configuration and troubleshooting guidance.
- [x] Run full lint/test suites (backend + Studio) and address performance or accessibility findings.
- [x] Prepare release notes and rollout checklist for enabling the Trace tab in staging and production environments.
