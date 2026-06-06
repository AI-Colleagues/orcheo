# Requirements Document

## METADATA
- **Authors:** Codex
- **Project/Feature Name:** Containerless Trusted Workflows
- **Type:** Enhancement
- **Summary:** Remove external CLI-agent support and gVisor sandbox machinery by making production workflow ingestion and execution declarative/trusted-node only. Self-hosted and developer deployments may opt into unsafe arbitrary-code workflows for convenience, but production must reject them at ingestion and execution.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-06-05

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Prior Artifacts | `project/initiatives/workspace_runtime_isolation/1_requirements.md` | Shaojie Jiang | Workspace runtime isolation requirements |
| Prior Artifacts | `project/initiatives/workspace_runtime_isolation/2_design.md` | Shaojie Jiang | Workspace runtime isolation design |
| Prior Artifacts | `project/initiatives/external_agent_cli_nodes/1_requirements.md` | Shaojie Jiang | External agent CLI node requirements |
| Related Initiative | `project/initiatives/python_only_workflow_composition/1_requirements.md` | Shaojie Jiang | Python-only workflow composition requirements |
| Eng Requirement Doc | `project/initiatives/containerless_trusted_workflows/2_design.md` | Shaojie Jiang | Containerless trusted workflows design |
| Project Plan | `project/initiatives/containerless_trusted_workflows/3_plan.md` | Shaojie Jiang | Containerless trusted workflows plan |

## PROBLEM DEFINITION
### Objectives
Reduce workflow runtime latency, resource overhead, and operational complexity by removing gVisor sandbox containers and the external CLI-agent feature set. Preserve production multi-tenant safety by rejecting arbitrary-code workflows and executing only trusted declarative workflow graphs.

### Target users
- Platform operators running Orcheo in production SaaS environments
- Self-hosted operators who want simple local deployments and accept unsafe arbitrary-code tradeoffs
- Backend and SDK engineers maintaining workflow ingestion and execution
- Candidate workflow authors publishing installable workflow templates

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Platform operator | Run production workflows without gVisor containers | Workflow execution has lower resource and latency overhead | P0 | Production stack starts without sandbox-runtime, workspace-sandbox image, Docker socket mounting, or gVisor runtime dependency |
| Security reviewer | Reject arbitrary-code workflows in production | Removing sandbox containers does not create a tenant escape path | P0 | Production rejects Python-source, custom-node, raw-callable, plugin-node, code-node, JS-node, and CLI-agent workflows at ingestion and execution |
| Self-hosted operator | Opt into unsafe workflow flexibility | Local/private deployments can keep custom/plugin/Python workflows when tenant isolation is not required | P0 | A documented explicit setting enables unsafe arbitrary-code workflows outside production, with clear warnings |
| Workflow author | Submit workflow definitions without running code on the server | Ingestion is safe and deterministic | P0 | Ingestion accepts declarative graph payloads and does not execute Python scripts, import modules, or call entrypoints |
| Candidate catalog user | Browse candidates without server-side execution of remote Python | The catalog remains safe after sandbox removal | P0 | Candidate previews are generated from declarative manifests/frontmatter or omitted; remote candidate Python is never executed for preview enrichment |
| Canvas/SDK user | Request Mermaid previews when needed | Ingestion does not do extra rendering work | P1 | Mermaid is generated from stored declarative graph summaries on read/request, not required in ingestion output |

### Context, Problems, Opportunities
Orcheo currently uses gVisor workspace containers to isolate three risky surfaces: external CLI agents, workflow ingestion that executes Python scripts, and workflow execution that rebuilds graphs from stored Python source. This design provides a real security boundary, but adds operational burden, image build complexity, Docker socket handling, network policy infrastructure, warm-pool management, memory overhead, and latency.

The opportunity is to remove the need for sandbox containers by changing the trust model: production no longer accepts arbitrary executable workflow definitions. Workflows become declarative graph payloads made of reviewed first-party node types. Self-hosted and developer deployments may retain unsafe arbitrary-code support because those environments can make a local trust decision, but that mode must not be production default.

### Product goals and Non-goals
Goals:
- Remove all external/CLI-agent support from active runtime, worker, API, SDK, Canvas, tests, docs, and container images.
- Remove all gVisor/container sandbox machinery from active production runtime.
- Stop executing workflow Python during ingestion.
- Enforce a trusted declarative workflow policy at both ingestion and execution.
- Provide explicit unsafe modes for self-hosted/developer deployments.
- Keep candidate onboarding without executing remotely sourced Python.
- Move Mermaid generation out of ingestion and into lazy read/request paths.

Non-goals:
- Backward compatibility for unreleased existing workflows.
- Migration of existing script-backed workflow versions.
- Preserving external CLI-agent workflows.
- Providing tenant isolation for unsafe arbitrary-code mode.
- Building a Python static analyzer that claims to secure arbitrary code.

## PRODUCT DEFINITION
### Requirements
- **P0: Remove external/CLI-agent support**
  - Remove `ClaudeCodeNode`, `CodexNode`, `GeminiNode`, `ExternalAgentNode`, and legacy duplicate external-agent node surfaces from active imports and registry registration.
  - Remove external-agent runtime managers, auth file handling, login/status worker flows, provider models, and Canvas/API surfaces.
  - Remove CLI-agent installs from the workspace sandbox image path as the image/runtime is retired.
  - Remove or replace workflow remediation/autofix behavior that depends on CLI agents.

- **P0: Remove sandbox runtime machinery**
  - Remove sandbox-runtime service, workspace-sandbox image, gVisor/runsc configuration, Docker socket control service, sandbox managers, remote sandbox clients, credential relay paths that exist only for child sandboxes, egress proxy/nftables-only sandbox wiring, warm-pool metrics, and sandbox-specific docs/tests.
  - Remove worker/backend boot requirements for `ORCHEO_SANDBOX_RUNTIME_URL`, `ORCHEO_SANDBOX_CONTROL_TOKEN`, `ORCHEO_CONTAINER_RUNTIME`, and sandbox-only image/network variables.
  - Preserve ordinary workflow credential resolution through the existing vault/runtime context, not run-scoped sandbox broker tokens.

- **P0: Production trusted-workflow policy**
  - Add a production-safe policy that rejects arbitrary-code workflow formats at ingestion and execution.
  - Trusted production workflows must be declarative graph payloads whose nodes round-trip as `{type, config}` and whose node types are in a reviewed production trust allowlist.
  - Reject unknown registry nodes, plugin nodes unless explicitly trusted, raw functions, methods, lambdas, closures, code/script nodes, JavaScript sandbox nodes unless deliberately trusted, external/CLI-agent nodes, and any non-serializable runnable.
  - Enforce this policy at both version creation and workflow execution.

- **P0: Unsafe modes for self-hosted/developer deployments**
  - Provide explicit configuration for `production`, `self_host_unsafe`, and `developer` workflow trust modes.
  - `production` must be the SaaS/stack production mode and must reject arbitrary code.
  - `self_host_unsafe` and `developer` may permit Python-source/custom/plugin workflows but must display/log that there is no tenant isolation guarantee.
  - Production deployment templates must not omit or weaken the production policy.

- **P0: Stop workflow execution at ingestion time**
  - Ingestion must not execute Python, import workflow modules, call entrypoints, or compile/eval user source.
  - Replace script execution-derived metadata with declarative metadata supplied in the payload or candidate manifest.
  - Reject Python-source ingestion in production unless converted to a declarative manifest before upload.

- **P0: Declarative workflow graph format**
  - Introduce or standardize a declarative graph schema with nodes, edges, conditional edges, triggers, listeners, required credentials, metadata, and runnable config.
  - Backend graph building must construct LangGraph graphs from this declarative format using trusted registered node constructors.
  - Execution must validate the graph again before building/running it.

- **P0: Candidate onboarding without SDK dependency**
  - Candidate catalog entries must include a declarative manifest, either in `config.json`, frontmatter, or a dedicated workflow manifest file.
  - Candidate catalog parsing may read remote files and frontmatter but must not execute remote Python.
  - Candidate Mermaid previews should be generated from declarative manifests or omitted until available.

- **P1: Mermaid on demand**
  - Mermaid is not required in ingestion output.
  - Backend/SDK/Canvas may request or compute Mermaid from stored declarative graph summaries when rendering a version or candidate preview.
  - Cached Mermaid is optional and must be treated as derived data.

- **P1: Import scanning as advisory defense-in-depth**
  - If unsafe Python ingestion remains available for self-host/developer modes, AST import/call scanning may provide lint warnings or hard errors in those modes.
  - Import scanning must not be treated as the production security boundary.

### Designs (if applicable)
See `project/initiatives/containerless_trusted_workflows/2_design.md`.

### Other Teams Impacted
- **Backend/Worker:** Execution path changes from script rebuild plus optional sandbox dispatch to declarative graph validation/build/run.
- **SDK/CLI:** Upload format changes from Python script-first to manifest/declarative graph-first; optional local conversion helpers may remain.
- **Canvas:** Template and candidate flows must submit/read declarative graph manifests and request Mermaid lazily.
- **Deployment:** Production stack removes sandbox services and enforces trusted-workflow mode.
- **Docs/Examples:** All examples using CLI agents, sandbox setup, or production Python-source ingestion must be rewritten or removed.

## TECHNICAL CONSIDERATIONS
### Architecture Overview
The production execution model becomes a trusted interpreter over declarative workflow definitions. The backend validates a stored workflow graph against an allowlist of first-party node types, constructs node instances from JSON config, builds the LangGraph graph, resolves credentials through the normal vault context, and executes in the worker process. No sandbox container is involved because production input is no longer arbitrary code.

Unsafe local modes keep convenience at the cost of isolation. They may execute Python-source or plugin/custom workflows in process, but those modes must be explicit and documented as unsuitable for untrusted multi-tenant production.

### Technical Requirements
- Define `WorkflowTrustMode` or equivalent config with production-safe defaults for SaaS/stack production.
- Define a `TrustedWorkflowPolicy` validator used by ingestion and execution.
- Define declarative graph schemas and validation errors that can identify unsupported nodes/formats clearly.
- Remove `load_graph_from_script` from production execution paths.
- Replace ingestion output dependencies on `summary` and `index` with schema-derived summary fields.
- Replace cron/listener extraction from executed graph objects with extraction from declarative node configs.
- Render Mermaid from declarative graph summary on demand.
- Update candidate catalog parser to prefer declarative manifests and avoid preview execution.

## MARKET DEFINITION
Internal platform/runtime enhancement; no external market analysis required.

## LAUNCH/ROLLOUT PLAN

### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Production arbitrary-code ingestion | 0 accepted Python-source/custom-node workflows in production |
| [Primary] Sandbox runtime dependency | 0 production services requiring gVisor/runsc, sandbox-runtime, workspace-sandbox image, or Docker socket control |
| [Secondary] Workflow startup overhead | Reduced p95 short-run overhead versus sandboxed baseline |
| [Secondary] Candidate catalog safety | 0 remote candidate Python executions during catalog fetch/preview |
| [Guardrail] Trusted workflow rejection accuracy | Unsupported formats fail with actionable errors at ingestion and execution |

### Rollout Strategy
This is a breaking internal change. Because Orcheo is not public yet, no migration/backward compatibility path is required. Land in phases: remove CLI-agent feature surfaces, add declarative trusted workflow ingestion/execution, switch production deployment to trusted mode, then remove sandbox infrastructure.

### Estimated Launch Phases

| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Internal development | Remove external/CLI-agent support and update templates/docs/tests that reference it |
| **Phase 2** | Internal development | Add declarative graph schema, trusted policy validation, execution-time enforcement, and Mermaid-on-demand rendering |
| **Phase 3** | Internal staging | Replace candidate onboarding preview execution with manifest-derived previews or no previews |
| **Phase 4** | Production stack | Remove sandbox services/images/config and enforce production trusted-workflow mode |

## HYPOTHESIS & RISKS
Hypothesis: shifting production workflows from arbitrary Python scripts to trusted declarative graphs eliminates the need for gVisor containers while preserving production tenant safety and reducing runtime overhead.

Risks:
- Policy gaps could allow arbitrary code through a supposedly trusted declarative path.
- Removing CLI agents and Python-source production ingestion reduces authoring flexibility.
- Self-host unsafe mode may be misunderstood as tenant-safe.
- Candidate repositories must be updated to include manifests before previews/install flows are fully functional.

Risk mitigation:
- Fail closed at ingestion and execution.
- Keep the trusted node allowlist explicit and reviewed.
- Add tests with malicious script-like payloads, lambdas, unknown nodes, plugin nodes, and non-serializable runnables.
- Make unsafe mode names and logs explicit: no tenant isolation guarantee.
- Update candidate authoring docs to require manifests.

## APPENDIX
Definitions:
- **Trusted declarative workflow:** A JSON-like workflow graph whose behavior is fully represented by first-party reviewed node types and configuration.
- **Arbitrary-code workflow:** Any workflow requiring execution of user Python, raw callable objects, plugins/custom nodes, code nodes, external CLI agents, dynamic imports, or non-serializable runnables.
- **Production mode:** The mode used by SaaS/multi-tenant deployments; arbitrary-code workflows are rejected.
- **Unsafe mode:** A self-host/developer convenience mode that may execute arbitrary code and therefore provides no tenant isolation guarantee.
