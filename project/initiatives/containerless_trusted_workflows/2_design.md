# Design Document

## For Containerless Trusted Workflows

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-06-05
- **Status:** Draft

---

## Overview

This design removes Orcheo's gVisor-based workflow sandboxing by changing what production accepts as executable workflow input. Production no longer ingests or executes arbitrary Python workflow scripts. Instead, it accepts only trusted declarative workflow graphs composed of reviewed first-party node types and JSON-serializable configuration.

The external CLI-agent feature set is removed entirely. Claude Code, Codex, Gemini, and their runtime/auth/login surfaces are not production workflow nodes, and they no longer justify keeping workspace containers, agent CLI installs, Docker exec paths, or sandbox runtime pools.

Self-hosted and developer deployments can explicitly enable unsafe arbitrary-code workflows for convenience. That mode is intentionally not tenant-safe. It is a local trust decision, not a replacement for the removed sandbox boundary.

## Components

- **Workflow Trust Mode (Backend / Config)**
  - Defines `production`, `self_host_unsafe`, and `developer` modes.
  - Production mode rejects arbitrary-code formats at ingestion and execution.
  - Unsafe modes may allow Python-source/custom/plugin workflows with clear warnings.

- **Trusted Workflow Policy (Core / Backend)**
  - Validates graph payloads before storing versions and before execution.
  - Accepts only declarative graphs made of reviewed trusted node types.
  - Rejects raw callables, lambdas, unknown nodes, plugin nodes unless explicitly trusted, code/script nodes, JS sandbox nodes unless explicitly trusted, external-agent nodes, and non-serializable runnables.

- **Declarative Graph Schema (Core / SDK / Canvas)**
  - Canonical workflow representation for production.
  - Contains nodes, edges, conditional edges, triggers, listeners, credential references, metadata, and runnable config.
  - Provides enough structure to build a LangGraph graph without executing workflow source code.

- **Declarative Graph Builder (Core)**
  - Builds LangGraph `StateGraph` instances from declarative graph payloads.
  - Resolves node constructors through the trusted registry allowlist.
  - Never imports tenant modules or calls tenant entrypoints in production mode.

- **Mermaid Renderer (Backend / SDK / Canvas)**
  - Generates Mermaid from declarative graph summaries on demand.
  - Treats Mermaid as derived/cached output, not required ingestion data.

- **Candidate Catalog Parser (Backend)**
  - Reads candidate metadata, frontmatter, and declarative manifests from the candidates repository.
  - Does not execute candidate `workflow.py` files or use sandboxed preview enrichment.
  - Produces previews from manifests when present; otherwise returns candidates without Mermaid.

- **Unsafe Python Loader (Optional / Self-host Developer Only)**
  - If retained, runs Python-source workflows in process only when explicitly enabled.
  - May perform AST import/call scanning for warnings or additional local policy enforcement.
  - Must not be reachable in production mode.

- **Retired Components**
  - Sandbox runtime service, workspace sandbox image, `runsc` runtime dependency, remote sandbox manager/executor, credential relay paths only needed by sandboxes, egress proxy/nftables sandbox wiring, warm-pool autoscaling/metrics, and external CLI-agent runtimes.

## Request Flows

### Flow 1: Production Declarative Ingestion

1. Client submits a workflow version payload with `format: "orcheo-declarative-graph"` and graph metadata.
2. Backend validates schema shape and required fields.
3. `TrustedWorkflowPolicy` validates every node, edge, conditional edge, trigger, listener, and credential reference.
4. Backend derives summary metadata directly from the declarative graph.
5. Backend stores the workflow version without executing Python, importing modules, or rendering Mermaid.
6. Response returns the stored version and summary metadata.

### Flow 2: Production Workflow Execution

1. Trigger/API enqueues a workflow run.
2. Worker loads the stored graph payload.
3. Worker re-runs `TrustedWorkflowPolicy` before execution.
4. Declarative graph builder constructs node instances from trusted registry metadata and JSON config.
5. Worker executes the LangGraph graph in process using normal vault credential resolution.
6. Run history and output are persisted as they are today.

### Flow 3: Mermaid Read/Preview

1. Canvas, SDK, or backend version serializer needs a diagram.
2. Renderer checks for cached/generated Mermaid if available.
3. If absent, renderer builds Mermaid from declarative graph summary.
4. Response includes Mermaid; stored workflow graph does not require an `index.mermaid` field.

### Flow 4: Candidate Catalog Fetch

1. Backend downloads the candidate repository tarball.
2. Parser reads frontmatter, `config.json`, and/or a dedicated declarative manifest.
3. Parser builds `CandidateItem` records without executing `workflow.py`.
4. If a manifest includes graph summary, backend can lazily derive Mermaid for previews.
5. If no manifest is present, backend returns candidate metadata without Mermaid and marks install unavailable or requiring manifest update.

### Flow 5: Unsafe Self-host Python Ingestion

1. Operator explicitly sets workflow trust mode to `self_host_unsafe` or `developer`.
2. Client submits Python-source workflow payload.
3. Backend logs a warning that arbitrary code will execute without tenant isolation.
4. Optional AST scanning rejects or warns on disallowed imports/calls.
5. Loader executes the workflow source in process and stores/executes it according to unsafe-mode behavior.

## API Contracts

### Create Workflow Version From Declarative Graph

```http
POST /api/workflows/{workflow_ref}/versions
Headers:
  Authorization: Bearer <token>
  X-Orcheo-Workspace: <workspace>
Body:
  {
    "graph": {
      "format": "orcheo-declarative-graph",
      "version": 1,
      "nodes": [
        {
          "id": "fetch_rss",
          "type": "RSSNode",
          "config": {
            "name": "fetch_rss",
            "sources": ["https://example.com/feed.xml"]
          }
        }
      ],
      "edges": [
        {"source": "START", "target": "fetch_rss"},
        {"source": "fetch_rss", "target": "END"}
      ],
      "conditional_edges": [],
      "triggers": [],
      "listeners": [],
      "credential_references": []
    },
    "runnable_config": {},
    "metadata": {},
    "notes": "string",
    "created_by": "string"
  }

Response:
  201 Created -> WorkflowVersion
  400 Bad Request -> schema validation failure
  403 Forbidden -> workflow trust policy violation
```

### Ingest Python Source In Unsafe Mode Only

```http
POST /api/workflows/{workflow_ref}/versions/ingest
Headers:
  Authorization: Bearer <token>
  X-Orcheo-Workspace: <workspace>
Body:
  {
    "script": "python source",
    "entrypoint": "build_graph"
  }

Response:
  201 Created -> WorkflowVersion when mode is self_host_unsafe/developer
  403 Forbidden -> production mode rejects Python-source ingestion
```

### Render Mermaid

```http
GET /api/workflows/{workflow_ref}/versions/{version_id}/mermaid
Headers:
  Authorization: Bearer <token>
  X-Orcheo-Workspace: <workspace>

Response:
  200 OK -> { "mermaid": "graph TD..." }
  422 Unprocessable Entity -> graph cannot be rendered
```

## Data Models / Schemas

### Workflow Trust Mode

| Mode | Production Safe | Behavior |
|------|-----------------|----------|
| `production` | Yes | Declarative trusted workflows only; arbitrary-code formats rejected |
| `self_host_unsafe` | No | Allows custom/plugin/Python workflows for single-tenant/local trust contexts |
| `developer` | No | Permissive local mode for development and tests |

### Declarative Workflow Graph

```json
{
  "format": "orcheo-declarative-graph",
  "version": 1,
  "nodes": [
    {
      "id": "node_id",
      "type": "TrustedNodeType",
      "config": {
        "name": "node_id"
      }
    }
  ],
  "edges": [
    {
      "source": "START",
      "target": "node_id"
    }
  ],
  "conditional_edges": [
    {
      "source": "node_id",
      "branch": "condition_name",
      "mapping": {
        "case": "target_node"
      },
      "default": "fallback_node"
    }
  ],
  "triggers": [],
  "listeners": [],
  "credential_references": [],
  "metadata": {}
}
```

### Trusted Node Policy Entry

| Field | Type | Description |
|-------|------|-------------|
| node_type | string | Registry metadata name |
| source | enum | `first_party`, `trusted_plugin` |
| production_allowed | bool | Whether the node can run in production mode |
| accepts_code | bool | Must be false for production-trusted nodes |
| accepts_network | bool | Records whether node performs outbound network operations |
| credential_scopes | list[string] | Credential reference types the node can resolve |

### Candidate Manifest

```json
{
  "id": "candidate-id",
  "handle": "candidate-handle",
  "name": "Candidate Name",
  "description": "Short description",
  "entrypoint": null,
  "graph": {
    "format": "orcheo-declarative-graph",
    "version": 1,
    "nodes": [],
    "edges": []
  },
  "configurable_schema": {},
  "metadata": {}
}
```

## Security Considerations

- Production security depends on refusing arbitrary code, not on Python import scanning.
- Policy validation must run at ingestion and execution because stored graph payloads may be created or modified outside the normal ingestion path during development/tests.
- Trusted nodes must not execute tenant-provided code strings, raw callables, shell commands, dynamic imports, or plugin code unless the deployment explicitly trusts that plugin.
- Self-host unsafe and developer modes must log warnings and should expose clear UI/CLI messaging that tenant isolation is not provided.
- Candidate catalog fetches remote repository content; production must parse it as data only.
- Removing sandbox credential broker paths means credentials resolve in the worker process. That is acceptable only because production workflow behavior is trusted first-party node code.

## Performance Considerations

- Removing sandbox dispatch eliminates container acquire/release overhead, warm-pool memory, Docker exec overhead, and gVisor syscall/network overhead.
- Declarative graph validation adds CPU work at ingestion and execution, but should be small compared with workflow execution.
- Mermaid should be computed lazily and cached for list/gallery views if rendering becomes hot.
- Candidate catalog should avoid sequential expensive preview work during cache refresh; manifest-derived previews are cheap and deterministic.

## Testing Strategy

- **Unit tests**
  - Trust mode parsing and production defaults.
  - Trusted workflow policy accepts known first-party declarative nodes.
  - Policy rejects unknown nodes, plugin nodes by default, raw callables, lambdas, code nodes, JS sandbox nodes, external-agent nodes, and non-serializable configs.
  - Declarative graph summary and cron/listener extraction.
  - Mermaid rendering from declarative summary.

- **Integration tests**
  - Production ingestion rejects Python-source payloads.
  - Production execution revalidates and rejects a stored unsafe payload.
  - Trusted declarative workflow executes in worker without sandbox runtime configuration.
  - Candidate catalog fetch does not call Python ingestion or sandbox preview enrichment.

- **Manual QA checklist**
  - Production stack boots without sandbox-runtime and without gVisor/runsc.
  - Canvas can create/open/run trusted declarative workflows.
  - Candidate tab loads when candidates omit Mermaid.
  - Self-host unsafe mode clearly warns before accepting Python-source workflows.

## Rollout Plan

1. Remove external/CLI-agent nodes and auth/runtime surfaces.
2. Add declarative schema and trusted policy, while tests still run both paths.
3. Switch production mode to declarative trusted workflows only.
4. Replace candidate preview enrichment with manifest-derived or omitted previews.
5. Remove sandbox runtime services, images, config, docs, and tests.
6. Remove dead Python-source production ingestion/execution paths or restrict them to unsafe modes.

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-06-05 | Codex | Initial draft |
