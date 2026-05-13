# Design Document

## For Agent Training Runtime & AgentensorNode

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2025-12-16
- **Status:** Approved

---

## Overview

This feature enables Orcheo workflows to accept per-run `langchain_core.runnables.RunnableConfig` payloads so authors can tune tracing, concurrency, callbacks, and metadata without code edits. The runtime must validate and propagate the config across LangGraph execution while preserving defaults and observability guarantees.

We also introduce an AgentensorNode inspired by `https://github.com/AI-Colleagues/agentensor/blob/main/examples/train.py`. It runs compiled workflows against evaluation datasets and evaluator functions in two modes: evaluation-only (benchmarking) and training (prompt optimization with checkpointed configs). The node standardizes experiment orchestration, produces reproducible metrics, and returns optimized runnable configs that can be reused in subsequent runs.

Key goals: runtime compatibility with LangChain config schema, reproducible evaluations, controlled training loops with safe resource usage, and artifact persistence (metrics, checkpoints).

Training and evaluation modes execute against the same compiled workflows used for standard runs; the trainer augments the runtime with dataset iteration, evaluator orchestration, and prompt updates without changing the workflow authoring model.

## Components

- **Workflow Runtime (FastAPI/WebSocket + LangGraph)**
  - Accepts workflow run requests with `RunnableConfig`, merges with defaults, and injects into compiled graphs.
  - Maintains per-run metadata and emits status/progress events.
- **Orcheo Agentensor Package (packages/agentensor)**
  - Forked copy of upstream agentensor providing tensors, optimizers, and judges; pulled in as a first-party dependency.
  - Mirrors existing `packages/sdk` tooling (uv metadata, Ruff/mypy) and exposes integration points consumed by AgentensorNode with shimmed types/definitions in `src/orcheo/`.
- **Config Validator/Serializer (Platform)**
  - Validates JSON-serializable `RunnableConfig`, strips unsafe callbacks, enforces recursion/concurrency limits, and ensures trainable prompts are valid `TextTensor` definitions.
  - Validates config value references (including prompt references from nodes) and preserves `TextTensor` typing/metadata when dereferencing using existing `decode_variables()` / `_decode_value()` interpolation logic.
  - Persists normalized configs with runs and checkpoints.
- **AgentensorNode (LangGraph node)**
  - Coordinates dataset iteration, evaluator execution, and optimizer loops; exposes evaluation and training modes.
  - Produces checkpoints containing optimized prompts/configs and associated metrics.
- **Evaluators & Datasets (ML/Evals)**
  - Pluggable evaluator functions (LLM-judged or deterministic) and versioned datasets referenced by the trainer.
- **Checkpoint Store (Storage/Platform)**
  - Stores optimized configs and metrics in a dedicated DB table keyed by `workflow_id` with versioned config records so the runtime can load the latest or a specific version.
  - Exposes download/reference links for reuse.
- **Observability & Safeguards (SRE/Platform)**
  - Tracing/metrics hooks, rate limits, and timeout/concurrency controls for trainer-heavy workloads.

## Request Flows

### Orcheo Integration Details

- **Node registration**: AgentensorNode is registered via `src/orcheo/nodes/registry.py` alongside other `TaskNode` subclasses so builders can reference it through the existing node discovery mechanism.
- **Inheritance**: The node inherits `TaskNode` to reuse state handling and config decoding, including `decode_variables()` and `_decode_value()` for `{{path.to.value}}` interpolation of `TextTensor` prompts.
- **Variable consistency**: Trainable prompts must be referenced from agent nodes via config paths to ensure the runtime resolves prompt tensors consistently across evaluation and training modes.


### Flow 1: Workflow Run with RunnableConfig

1. Client calls `POST /api/workflows/{workflow_id}/runs` with inputs and optional `runnable_config`.
2. API layer validates and normalizes the config; merges with workflow defaults.
3. Runtime initializes compiled LangGraph with merged config and starts execution.
4. Nodes receive config context; tracing/metadata callbacks are applied.
5. Results and run metadata (including config) are persisted and streamed to the client.

### Flow 2: AgentensorNode Evaluation Mode

1. Client triggers trainer (API/CLI) with compiled workflow reference, dataset, evaluators, and `runnable_config`.
2. AgentensorNode iterates through dataset cases, executes workflow per case with provided config.
3. Evaluators score outputs; scores are aggregated.
4. Progress and aggregated metrics are streamed; response returns evaluation results only.

### Flow 3: AgentensorNode Training Mode

1. Client triggers trainer with training mode, optimizer settings, dataset, evaluators, and initial `runnable_config`.
2. AgentensorNode runs workflow per case, collects evaluator feedback, and updates prompt tensors/fields marked trainable (defined in config as `TextTensor` and referenced by agent nodes).
3. At checkpoints, the node evaluates the current config, records metrics, and persists checkpoint artifacts.
4. Training completes when convergence/epoch limits are reached; best config and evaluation summary are returned.

## API Contracts

```
POST /api/workflows/{workflow_id}/runs
Body:
  input: object
  runnable_config: RunnableConfig (optional)
  mode: "execute" | "evaluate" | "train" (optional; default "execute")
Response:
  202 Accepted -> { run_id, status_url, websocket_url }
  400 -> validation errors for config or payload
```

```
POST /api/workflows/{workflow_id}/runs
Body:
  input: object
  runnable_config: RunnableConfig (optional)
  mode: "execute" | "evaluate" | "train"
  trainer: {
    dataset_id: string,
    evaluators: EvaluatorRef[],
    optimizer?: { type: string, epochs: int, checkpoint_interval: int, max_concurrency?: int }
  }
Response:
  202 Accepted -> { run_id, status_url, websocket_url, checkpoint_urls? }
```

The trainer/evaluator mode reuses the existing workflow router and WebSocket streaming implementation so clients receive progress, per-case errors, evaluation summaries, and checkpoint metadata on the same channel as standard runs. Authentication/authorization mirrors existing workflow run policies.

## Data Models / Schemas

| Field | Type | Description |
|-------|------|-------------|
| runnable_config | object | LangChain-compatible config: `configurable`, `tags`, `metadata`, `callbacks`, `recursion_limit`, `max_concurrency`, `run_name`, and optional `prompts` mapping `prompt_name -> TextTensor` (agent nodes reference these by config path at runtime) |
| trainer_request | object | `{ workflow_id, dataset_id, evaluators, mode, runnable_config, optimizer }` |
| checkpoint | object | `{ id, workflow_id, config_version, runnable_config, metrics, created_at, artifact_url }` |
| evaluator | object | `{ id, type: "llm" | "deterministic", entrypoint: string, config?: object }`; entrypoints must be importable callables compatible with existing evaluator registry |

**Evaluation payload:** `{ dataset: { id?: string, cases: [{ inputs: object, expected_output?: any, metadata?: object }] }, evaluators: [{ id, entrypoint, config?: object }], max_cases?: int }`. Entrypoints resolve to callables/classes that accept `(EvaluationContext)` and return a `value`/`reason` pair; results stream via the existing WebSocket channel as `evaluation_progress` and `evaluation_summary` events.

Example `RunnableConfig` payload:

```json
{
  "configurable": { "system_prompt_trainable": true },
  "tags": ["agent-training", "checkpoint-0"],
  "metadata": { "experiment": "agenttrainer-v1" },
  "max_concurrency": 4,
  "recursion_limit": 25,
  "run_name": "trainer-eval",
  "prompts": {
    "agent_greeting": {
      "text": "You are a helpful assistant.",
      "type": "TextTensor",
      "requires_grad": true,
      "metadata": { "locale": "en-US" }
    }
  }
}
```

`TextTensor` entries must include `text` (string), `type="TextTensor"`, optional `requires_grad` (bool), and optional `metadata` (object). Validation rejects non-serializable fields and ensures values resolve through `{{config.prompts.<key>}}` interpolation.

Agent nodes refer to trainable prompts by config path (e.g., `config.prompts.agent_greeting`) in their own definitions; resolution is unidirectional from node -> config, so no prompt binding block is required in the config.

## Storage and Migrations

- **Checkpoint table**: `agentensor_checkpoints(workflow_id, id, config_version, runnable_config, metrics, created_at, artifact_url)` with indexes on `workflow_id` and `config_version`.
- **Dual backend support**: migrations provided for SQLite (local/dev) and PostgreSQL (staging/prod) using the existing migration toolchain; defaults ensure JSON/text column compatibility per backend.
- **Run metadata linkage**: run records store checkpoint references so download/reuse works through the existing run metadata surfaces.

## Security Considerations

- Authenticate all trainer and run requests; enforce workflow-level authorization.
- Validate configs to block arbitrary code injection via callbacks or unserializable objects.
- Redact secrets from configs and evaluator outputs before persistence; honor workspace boundaries for datasets/checkpoints.
- Rate limit training runs and cap concurrency to protect shared model gateways.

## Performance Considerations

- Bound `max_concurrency` and `recursion_limit` to safe defaults; allow overrides within limits.
- Stream progress to avoid large response payloads; paginate evaluator results for large datasets.
- Cache compiled graphs where possible; reuse model clients across cases to reduce cold starts.
- Monitor latency and error rates; fallback to baseline config if optimizer diverges.

## Testing Strategy

- **Unit tests**: config validation/merging (including `TextTensor` schema), trainer optimizer steps, evaluator aggregation.
- **Integration tests**: API payload validation, workflow run with `RunnableConfig`, trainer flows in evaluation and training modes with mock datasets, compatibility across existing node types.
- **Performance tests**: concurrent training/evaluation runs exercising `max_concurrency` and timeout guardrails.
- **Migration tests**: apply/rollback SQLite and PostgreSQL migrations for checkpoint storage, verify run metadata linkage.
- **Manual QA checklist**: run with/without config, long-running training with checkpoints, resume/download checkpoint, evaluator failure surfaces correctly, WebSocket streaming matches existing workflow run behavior.

## Rollout Plan

1. Phase 1: Enable config ingestion and trainer evaluation mode in staging behind feature flags; validate observability and schema.
2. Phase 2: Pilot training mode on internal workflows with capped datasets/concurrency; collect metrics and tune safeguards.
3. Phase 3: Enable opt-in for all workflows; document CLI/API usage and migrate key workloads.

Include feature flags for `runnable_config_enabled` and `agent_trainer_enabled`; maintain backward compatibility for runs without configs.

## Open Issues

- [ ] Define default evaluator registry and serialization format.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2025-12-16 | Codex | Initial draft |
