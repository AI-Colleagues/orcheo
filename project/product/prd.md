# Orcheo – Hybrid Workflow Automation Platform PRD

## METADATA
- **Authors:** Shaojie Jiang, Claude, Codex
- **Project name (if applicable):** Orcheo – Hybrid Workflow Automation Platform
- **Product Summary:** Orcheo delivers a hybrid workflow automation experience that combines a low-code visual designer with a Python SDK built on LangGraph, enabling teams to orchestrate AI-driven automations without sacrificing depth or control. The platform emphasizes secure integration management, real-time observability, and an extensible node ecosystem to cover common business and developer use cases.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 1 May 2025

## RELEVANT LINKS & STAKEHOLDERS
| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Roadmap & Project Board | [roadmap](./roadmap.md) | PM | Shaojie Jiang |
| Prior Artifacts | TBD | PM | Shaojie Jiang |
| PRD / Design Review | TBD | PM | Shaojie Jiang |
| Design File/Deck | TBD | Designer | TBD |
| Eng Requirement Doc | TBD | Tech Lead | TBD |
| Marketing Requirement Doc | TBD | PMM | TBD |
| Experiment Plan | TBD | DS | TBD |
| Product Rollout Docs | TBD | Product Ops | TBD |

## PROBLEM DEFINITION
### Objectives
Deliver a unified workflow automation platform that bridges low-code visual tooling with code-first extensibility. Validate that an AI-first foundation meaningfully improves automation adoption and execution success compared to incumbent solutions.

### Target users
- **Developer-platform engineers** who rely on the Python SDK (full-stack, backend, SaaS founders) to extend workflows with custom nodes, secure integrations, and version-controlled deployments.
- **Operations and integration specialists** responsible for cross-system automations that demand reliable triggers, credential governance, and actionable monitoring.
- **Data and AI practitioners** such as data scientists and ML engineers who need to orchestrate multi-model pipelines with reproducibility and deep observability.
- **Business and go-to-market owners** including business analysts and marketing managers who need a canvas-driven, low-code experience to launch automations without writing code.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Full-stack developer (Dev) | Use the Python SDK for complex integrations and custom nodes | I can extend workflows programmatically with tests and version control | P0 | SDK offers typed node authoring, local execution, and deployment hooks that stay in sync with server workflows |
| Backend developer (Jake) | Orchestrate API calls with secure credential management | I can integrate services safely without exposing secrets | P0 | Credential vault issues scoped tokens, masks secrets in logs, and enforces rotation policies for API-driven workflows |
| LangGraph developer (Nina) | Submit Python scripts defining graphs directly to the backend | I can reuse the same code-first authoring flow when deploying to Orcheo | P0 | Backend accepts LangGraph-compatible Python scripts, validates them, and returns import status or actionable errors |
| LangGraph developer (Priya) | Inspect workflows, nodes, and credentials from the terminal | I can manage, debug, and administer LangGraph-first projects without leaving the CLI | P0 | CLI lists nodes and workflows, prints Mermaid diagrams, and supports credential CRUD with status visibility |
| Data scientist (Lisa) | Chain AI models and analyses via a code-first approach | I can experiment with ML workflows while keeping full control | P0 | SDK supports orchestrating multiple AI nodes with dataset inputs, reproducible runs, and artifact tracking |
| Integration specialist (Chris) | Configure webhook and cron triggers with monitoring and alerts | I can keep cross-system automations reliable | P0 | Trigger setup supports retries, failure notifications, and visibility into recent executions |
| SaaS founder (Tom) | Combine Canvas-managed workflows with custom Python components | I can prototype quickly while retaining technical flexibility | P1 | A workflow can mix Canvas-configured steps with SDK-authored nodes and deploy as a single versioned flow |
| Business analyst (Maya) | Build multi-step data workflows on the visual canvas | I can launch data pipelines without writing code | P1 | A data workflow with transforms and conditionals can be created, validated, and executed entirely from the canvas |
| Marketing manager (Sam) | Assemble campaign automations through a low-code interface | I can streamline marketing processes without waiting on engineering | P1 | Templates plus drag-and-drop nodes let me schedule and publish an automation without touching the SDK |
| ML engineer (Amy) | Trace multi-step AI agent workflows with detailed logs | I can debug and optimize AI-powered automations | P1 | Execution viewer provides per-step prompts, responses, token metrics, and the ability to replay a run |

### Context, Problems, Opportunities
Current automation platforms force teams to choose between ease of use and advanced programmability, while AI-native capabilities are often bolted on rather than foundational. Orcheo targets this gap with an architecture that treats AI and LangGraph orchestration as core primitives, giving visual-first users guardrailed power and developers the depth they expect. Competitive analysis (Zapier, Airflow, n8n) highlights demand for faster experimentation, richer integration coverage, and lightweight deployment options—areas Orcheo addresses through a dual-mode backend and extensible node ecosystem.

### Product goals and Non-goals
**Goals**
- Deliver a cohesive dual experience that combines the visual designer and Python SDK while keeping LangGraph orchestration consistent across both modes.
- Anchor the platform on an AI-first architecture with an extensible node catalog so teams can cover common integrations without sacrificing depth.
- Provide production-grade integration management with secure credential handling to support reliable, testable automations.
- Ship baseline execution visibility and trigger coverage (webhook, cron, manual) that validate reliability for core automation patterns.
- Advanced monitoring, alerting, or step-level debugging toolsets.

**Non-goals**
- Enterprise SSO and advanced RBAC controls.
- Multi-workspace, shared workspaces, or collaboration features beyond single-workspace usage.
- Marketplace monetization or community node distribution at launch.
- Native mobile clients or deep on-premises deployment support in v1.0.

## PRODUCT DEFINITION
### Requirements
#### Frontend (React Flow Canvas)
- Drag-and-drop workflow designer with validation, execution monitoring, and template-driven onboarding.
- Canvas tooling covers pan/zoom/minimap navigation, grid snapping, undo/redo shortcuts, node search/filtering, collapsible configuration panels, duplication, and custom styling.
- Workflow ops include save/load, JSON import-export, templates/examples, version history diffs, and shareable exports.
- Node library surfacing 20+ integrations across AI, data, communication, and control nodes.
- Credential management UI, workflow versioning, and reusable sub-workflows.

#### Backend (Dual-Mode Architecture)
- Python library mode exposing LangGraph-powered node definitions and execution orchestration.
- Standalone FastAPI server provides workflow CRUD, execution control, and WebSocket streaming for execution telemetry.
- Backend accepts LangGraph-compatible Python script submissions for graph definition, mirroring LangGraph dev workflows so remote authoring feels identical to local SDK usage.
- Python SDK ships an `HttpWorkflowExecutor` that wraps workflow run APIs with `httpx`, automatic bearer authentication, and configurable retry/backoff semantics for reliable remote dispatches.
- Trigger layer ships webhook validation (multi-verb support, request filtering, custom responses, rate limiting), cron scheduling (timezone aware, overlap guards, pause/resume), and manual runs with batch inputs plus debug mode.
- Execution engine with history, retries, and support for loops, branching, and parallelization.

#### Credential & Security Handling
- AES-256 encrypted vault with shareable credentials by default, plus optional workflow, workspace, or role scope policies, token rotation, and masked logs.
- Pre-built credential templates for popular services with validation before execution.
- Automatic OAuth refresh and credential testing to guard against misconfiguration.

#### Core Node Types (v1.0)
- Triggers: Webhook, Cron, Manual, HTTP Polling.
- AI / LLM: OpenAI, Anthropic, Custom AI Agent, Text Processing.
- Data & Logic: HTTP Request, JSON Processing, Data Transform, If/Else, Switch, Merge, Set Variable.
- Storage & Communication: MongoDB, PostgreSQL, SQLite, Email, Slack, Telegram, Discord.
- Utilities: Python/JavaScript code execution, Delay, Debug, Sub-workflow orchestration.

### Execution Flows
- **Visual designer path:** Canvas-built workflows convert to LangGraph format, validate server-side, persist, run via triggers, and emit live updates over WebSocket.
- **Code-first path:** Developers assemble LangGraph graphs with Orcheo nodes, execute locally, and submit the same Python scripts to the server for persistence, credential reuse, and monitoring without rewriting the graph definition.

### Designs (if applicable)
Figma mocks and copy docs are in progress; link will be attached after initial canvas prototype.

### [Optional] Other Teams Impacted
Not applicable.

## TECHNICAL CONSIDERATIONS
### Data Requirements
The platform primarily orchestrates external API data and transient workflow state; no custom model training data is required initially. Credential vault metadata and execution traces must be stored securely for analytics and debugging.

### Algorithm selection
Rely on hosted LLM APIs (OpenAI, Anthropic) combined with LangGraph orchestration for deterministic agent flows. Rule-based logic nodes and optional custom code blocks handle non-AI branching where probabilistic models are unnecessary.

### Model performance requirements
LLM-powered nodes should achieve >90% task success rate on scripted prompts, with latency under 5 seconds for synchronous executions. Guardrails must ensure deterministic fallbacks when AI confidence is low.

### Engineering resource
Initial delivery centers on a single full-stack builder covering product, backend, frontend, and DevOps, with occasional contract design or security reviews. Timeline assumes staggered milestones that account for context switching and prioritization by one primary contributor.

## MARKET DEFINITION
### Total Addressable Market
Targets SMB to mid-market teams and internal developer platforms seeking hybrid automation; near-term focus on North America and Europe where SaaS adoption is highest. Highly regulated on-premise-only environments remain out of scope for v1.0 due to hosting requirements.

### Launch Exceptions
| Market | Status | Considerations & Summary |
|--------|--------|--------------------------|
| Highly regulated industries requiring on-prem | In discussion | Hosting and compliance constraints defer launch until certified deployment model exists |

## LAUNCH PLAN
### Experiment Plan
Pilot with a closed beta of existing community members to validate usability of both canvas and SDK flows, followed by staged A/B testing of AI-assisted node recommendations versus manual configuration.

### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| Primary – uv/PyPI installs & GitHub stars | ≥2,000 uv (or PyPI) installs and ≥1,000 GitHub stars within six months to evidence community traction |
| Secondary – Quickstart completion rate | ≥60% of new community members complete the CLI quickstart within 14 days (instrumented or self-reported), proving accessible onboarding |
| Guardrail – Critical run-failure backlog | ≤2 open P0/P1 automation failure issues older than 14 days to preserve trust in reliability |

### Estimated Geo Launch Phases (if applicable)
| XP launch | [Insert Month] |   |
|-----------|----------------|---|
| **Phase 1 Launch** | Month 8 | North America & EU beta customers leveraging cloud-hosted deployment |
| **Phase 2 Launch** | Month 10 | Broader rollout to additional regions and partners once credential vault hardening and monitoring complete |

### Pricing strategy (if applicable)
Adopt a usage-based freemium model: free tier for personal workflows, paid tiers unlocking team features, advanced integrations, and higher execution quotas. Detailed pricing research remains TBD.

## HYPOTHESIS & RISKS
Primary hypothesis: Providing a dual-mode automation experience (visual + SDK) backed by AI-native nodes will accelerate workflow creation and increase automation success rates for both business and engineering users. Confidence is medium-high based on user interviews and market analysis of existing pain points.
Secondary hypothesis: Secure credential management and real-time observability will reduce operational friction enough to drive >20% uplift in retained workflows quarter over quarter. Confidence is medium pending beta validation.

**Operational risks & mitigations**
- React Flow performance under large graphs → mitigate with early load testing and canvas optimization.
- LangGraph integration complexity → mitigate through proof-of-concept spikes and incremental rollout.
- Node development bottlenecks → mitigate via contribution framework and prioritized integration roadmap.
- Security vulnerabilities → mitigate with vault penetration tests and secure coding reviews.
- Adoption hurdles → mitigate with continuous user research and template-driven onboarding.

## APPENDIX
### Revision history
| Rev | Date | Author(s) | Notes |
| --- | ------------ | ------------------- | ------------------------------- |
| 0.1 | 1 May 2025 | Shaojie Jiang | Initial skeleton |
| 0.2 | 21 Jul 2025 | Shaojie Jiang, ChatGPT, Kiro | CLI-first approach pivot |
| 1.0 | 6 Sep 2025 | Shaojie Jiang, Claude | Comprehensive workflow automation platform |

### Future roadmap (post v1.0)
- **v1.1 – Advanced Features:** Advanced debugging tools, team workspaces, workflow marketplace.
- **v1.2 – Enterprise:** SSO, audit logging, advanced monitoring, on-prem deployment options.
- **v2.0 – AI-Enhanced:** AI-assisted workflow creation, smart node recommendations, automatic error resolution, natural language workflow queries.
