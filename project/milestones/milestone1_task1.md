# Milestone 1 – Task 1: Architecture & Persistence Decisions

## Objective

Finalize the LangGraph-centric architecture, persistence layer, and hosting model
that will power both the code-first SDK and the future canvas experience.

## Summary of Decisions

### LangGraph-Centric Runtime

- **Single orchestration core** – All workflow execution flows through LangGraph to
  guarantee parity between SDK-authored and canvas-authored graphs.
- **State envelope** – We standardize on the existing `State` dataclass that extends
  `MessagesState`, ensuring AI and deterministic nodes share the same shape and can
  broadcast updates over WebSockets without bespoke adapters.
- **Graph builder contract** – The `build_graph` helper now remains the canonical
  entry-point for turning persisted JSON definitions into runnable LangGraph
  instances. This establishes a stable interface for both authoring modes.

### Persistence Layer

- **Primary backend: SQLite** – For local development and lightweight
  single-workspace deployments we use `langgraph-checkpoint-sqlite` backed by an
  application-managed `aiosqlite` connection. This keeps onboarding friction low
  while providing deterministic recovery for streaming runs.
- **Production backend: Postgres** – We introduced a pluggable persistence helper
  that can create either SQLite or Postgres checkpoint savers. Postgres is the
  recommended production target thanks to connection pooling support via
  `psycopg_pool` and better concurrency guarantees.
- **Configuration driven** – Persistence is now controlled through environment
  variables (`ORCHEO_CHECKPOINT_BACKEND`, `ORCHEO_SQLITE_PATH`,
  `ORCHEO_POSTGRES_DSN`, `ORCHEO_REPOSITORY_BACKEND`,
  `ORCHEO_REPOSITORY_SQLITE_PATH`). This allows operators to toggle storage backends without
  code changes and creates a clear path for managed hosting.

### Hosting Model

- **FastAPI application** – The backend remains a FastAPI service exposing REST and
  WebSocket endpoints. Server host/port are configurable via
  `ORCHEO_HOST`/`ORCHEO_PORT` to support containerized deployment or direct local
  execution.
- **Stateless API, stateful runtime** – Application instances stay stateless while
  workflow execution state is stored in the configured checkpoint backend. This
  separation makes it easy to scale API pods while delegating durability to the
  persistence layer.
- **SDK & Canvas parity** – Both modes invoke the same HTTP/WebSocket APIs which
  rely on the unified persistence helper. This ensures future canvas iterations
  inherit the exact runtime semantics used by SDK authors.

## Follow-Ups

- Document deployment recipes for Docker Compose and cloud hosting.
- Extend configuration helpers to cover credential vault settings when that system
  lands later in Milestone 1. **(Completed via Dynaconf vault configuration update.)**
- Wire Postgres-backed persistence into CI to validate connection pooling under
  load tests once available infrastructure is provisioned. **(Completed via
  GitHub Actions Postgres integration test job.)**
