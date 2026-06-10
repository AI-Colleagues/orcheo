# Orcheo Roadmap

This roadmap consolidates Orcheo's milestone sequencing and task backlog in a single place so planning and execution stay aligned.

## v1.0 Roadmap Summary
1. **Milestone 1 – Platform Foundation**: Lock in LangGraph architecture, scaffold repos, and ship baseline developer tooling.
2. **Milestone 2 – Backend Orchestration & Triggers**: Deliver the dual-mode execution backend, workflow APIs, and reliable trigger layer.
3. **Milestone 3 – Credential Vault & Security**: Launch the encrypted credential vault, validation flows, and security hardening.
4. **Milestone 4 – Visual Designer Experience**: Build the React Flow workflow view, workflow operations, and live execution monitoring.
5. **Milestone 5 – Node Ecosystem & Integrations**: Provide launch-ready trigger, AI, data, storage, and utility nodes with docs/tests.
6. **Milestone 6 – Observability, Testing & Launch Prep**: Instrument runtime visibility, finalize metrics, and prep the beta rollout.

## Milestone Details
### Milestone 1 – Platform Foundation
- [x] Finalize LangGraph-centric architecture decisions, persistence layer, and hosting model supporting both Studio and SDK. See [Milestone 1 Task 1](../milestones/milestone1_task1.md) for the detailed outcomes.
  - [x] Capture deployment recipes for local and hosted environments ([deployment guide](../../docs/deployment.md)).
  - [x] Extend configuration to cover vault-managed credential settings.
  - [x] Wire Postgres persistence checks into CI once infrastructure is ready.
- [x] Scaffold repositories for FastAPI backend, Python SDK package, and React Studio app, including CI, linting, and coverage automation.
- [x] Define workflow data models (graphs, versions, runs, credential metadata) with encryption hooks and audit logging.
- [x] Establish developer tooling: local dev containers, `uv` scripts, seed environment variables, and sample flows covering both user paths.
- [x] Publish the `orcheo` core package to PyPI and automate release versioning so downstream packages (backend, SDK) can depend on public artifacts. See [releasing guide](../../docs/releasing.md) for the package-by-package workflow.
- [x] Add smoke tests for the FastAPI deployment wrapper (import validation, app factory health) and expand CI coverage checks across workspace packages.

### Milestone 2 – Backend Orchestration & Triggers
- [x] Implement Python SDK with typed node authoring, local execution parity, and deployment hooks that sync with the server.
- [x] Build FastAPI services for workflow CRUD, execution lifecycle, version diffing, and WebSocket streaming telemetry.
- [x] Deliver trigger layer covering webhook validation (verbs, filtering, rate limits), cron scheduler (timezone aware, overlap guards), manual/batch runs, and retry policies.
  - [x] Implement webhook trigger configuration with verb filtering, shared secrets, and rate limiting.
  - [x] Build cron scheduler supporting timezone-aware execution and overlap protection.
  - [x] Support manual and batch run dispatch from the trigger layer.
  - [x] Introduce configurable retry policies for trigger-driven runs.
- [x] Layer in SDK HTTP execution helpers (httpx client, retry/backoff, auth headers) paired with integration tests against local backend deployments.
- [x] Add execution engine support for loops, branching, parallelization, run history, and replay/debug APIs.
- [x] Expose backend ingestion that accepts LangGraph Python scripts, converts them to workflow graphs, and preserves parity with LangGraph dev's authoring experience.

### Milestone 3 – Credential Vault & Security
- [x] Introduce a SQLite-backed developer repository with a pluggable storage abstraction so local workflows persist without requiring Postgres while keeping production defaults intact.
- [x] Ship AES-256 encrypted credential vault with shareable credentials, optional scope policies, rotation workflows, and masked logging.
- [x] Implement OAuth refresh flows, credential validation/testing, and health checks to block misconfigured automations.
- [x] Create credential templates and UI/API for secure storage, token issuance, and secret governance alerts.
- [x] Run security reviews, penetration tests, and threat modeling across vault, triggers, and execution surfaces. See [Milestone 3 Security Review](../milestones/milestone3_security_review.md) for the full report.

### Milestone 4 – Visual Designer Experience
- [x] Migrate the legacy Studio app to connect to the new backend via React Flow foundation, restoring minimap, snapping, and chat affordances while preserving node compatibility.
- [x] Finish React Flow workflow view tooling: add undo/redo history, node duplication/export handlers, node search/filtering, styling updates, and collapsible configuration panels.
- [x] Implement workflow operations: replace mocked save/load with persistence, wire JSON import/export, enable template onboarding, and surface a version diff viewer.
- [x] Integrate credential management UI, reusable sub-workflows, and publish-time validation flows throughout the workflow page.
- [x] Connect workflow executions to backend WebSocket streams for live status, token metrics, and run replay hooks.
- [x] Ship a ChatKit-inspired chat frontend (via OpenAI ChatKit or a custom equivalent) for testing workflows and production handoff.
- [x] Execute the [frontend experience plan](../milestones/frontend_plan.md) covering design system creation, architecture refactor, and QA expansion to de-risk the workflow page rebuild.

### Milestone 5 – Node Ecosystem & Integrations
- [x] Deliver trigger nodes (Webhook, Cron, Manual, HTTP Polling) with both UI and SDK parity.
- [x] Build basic nodes and utilities (If/Else, Switch, While, Set Variable, Delay, Sticky Note) with tests, docs, and templates.
- [x] Add single-node execution API endpoint and frontend integration for testing individual nodes in isolation (includes backend endpoint, frontend API client, and Node Inspector UI integration).
- [x] Launch LangGraph-aligned CLI tooling covering node discovery, workflow inspection with Mermaid output, and credential management (future MCP server reuse). See [CLI Tool Design](../architecture/cli_tool_design.md) for the full plan.
- [x] Design and implement ChatKit integration across demo frontend, backend endpoint, LangGraph workflow, and SQLite persistence (see [ChatKit Embedding Guide](../../docs/chatkit_integration/webpage_embedding_guide.md)).
- [x] Build Data & Logic nodes (HTTP Request, JSON Processing, Data Transform, Merge) plus Storage/Communication nodes (MongoDB, PostgreSQL, SQLite, Email, Slack, Telegram, Discord).
- [x] Add utility nodes (Python/JavaScript execution sandbox, Debug, Sub-workflow orchestration) with tests, docs, and templates.
- [x] Implement AgentNode with prompt management, MCP server connectivity, and latency guardrails.
- [x] Introduce a Guardrails node with workflow evaluation hooks for runtime quality checks and compliance reporting. (Plan dropped in favour of combining AgentNode with IfElseNode. Will be implemented in a future milestone.)

### Milestone 6 – Observability, Testing & Launch Prep
- [x] Implement end-to-end authentication layer (OIDC integration, service tokens, ChatKit session hardening, webhook signatures) per [Authentication System Design](../architecture/authentication_design.md).
- [x] ChatKit public page + workflow publish UX (CLI) and backend actions
- [x] Launch a dedicated workflow tracing tab with per-step prompts/responses, token metrics, artifact downloads, and monitoring dashboards.
- [ ] Establish success metrics tracking (uv installs, GitHub stars, quickstart completion rate, failure backlog) and analytics pipelines.
- [ ] Produce onboarding docs, templates, SDK examples, closed-beta playbook, and feedback/A-B testing loops for AI node recommendations.
- [ ] Run end-to-end reliability tests, load tests on the React Flow workflow view, finalize beta rollout plan, and prepare Phase 1/Phase 2 regional launch gates.

## Post v1.0 Outlook
- [ ] **v1.1 Advanced Features:** Team workspaces, advanced debugging, workflow marketplace.
- [ ] **v1.2 Enterprise:** SSO, audit logging, advanced monitoring, on-prem deployment.
- [ ] **v2.0 AI-Enhanced:** AI-assisted workflow creation, smart node recommendations, auto error resolution, natural language queries.

---

_Last updated: 2025-10-07_
