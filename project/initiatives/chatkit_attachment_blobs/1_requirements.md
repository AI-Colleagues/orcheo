# Requirements Document

## METADATA
- **Authors:** Codex
- **Project/Feature Name:** ChatKit Attachment Blob Storage
- **Type:** Enhancement
- **Summary:** Replace path-based ChatKit file attachment handling with scoped attachment records and database-backed blob storage by default. Public workflows remain able to accept unauthenticated uploads, but workflow state carries opaque attachment references instead of server filesystem paths or raw bytes.
- **Owner (if different than authors):** ShaojieJiang
- **Date Started:** 2026-05-27

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Existing ChatKit Integration | ../chatkit_integration/requirements.md | ShaojieJiang | ChatKit Integration |
| Existing Multi-workspace Design | ../multi_workspace/2_design.md | ShaojieJiang | Multi-workspace support for Orcheo |
| Design | 2_design.md | ShaojieJiang | ChatKit Attachment Blob Storage |
| Plan | 3_plan.md | ShaojieJiang | ChatKit Attachment Blob Storage |

## PROBLEM DEFINITION

### Objectives
Remove shared-filesystem and path-based trust from ChatKit file uploads while preserving public workflow uploads for unauthenticated users. Keep the first implementation operationally simple by storing attachment payloads in Postgres blob rows by default.

### Target users
- Public workflow visitors who upload files to published ChatKit workflows.
- Workflow creators who rely on ChatKit attachments for RAG, document loading, or conversational search workflows.
- Platform operators who need multi-workspace isolation without mandatory object-storage infrastructure.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Public workflow visitor | Upload a permitted file without logging in | I can use public workflows as intended | P0 | Upload succeeds for published workflows that allow public ChatKit access and returns an opaque attachment id |
| Workflow creator | Receive uploaded file content in my workflow | Existing document ingestion workflows continue to work | P0 | `DocumentLoaderNode` can resolve `attachment_id` into document content without reading arbitrary filesystem paths |
| Platform operator | Keep attachment payloads isolated by workspace/workflow/thread/session | A public upload in one workspace cannot be read by another workspace | P0 | Attachment reads require matching scope metadata and reject mismatches |
| Platform operator | Run Orcheo without S3/R2/MinIO | Self-hosted and local deployments work with only Postgres | P0 | Postgres blob table is the default backend and requires no external object store |
| Platform operator | Delegate blob payloads to object storage when configured | Larger deployments can optimize cost and throughput | P1 | Object storage is an explicit backend extension; metadata and access checks remain unchanged |

### Context, Problems, Opportunities
Current ChatKit direct uploads are written to a shared `CHATKIT_STORAGE_PATH`, and workflow execution later passes or reconstructs `storage_path` values for `DocumentLoaderNode`. That design makes the server filesystem part of the trust boundary: authorization is implicit in a path string rather than explicit attachment ownership metadata.

Public workflows complicate the obvious fix. Upload routes cannot simply require authenticated users because unauthenticated visitors must be able to upload files to published workflows. The correct boundary is therefore not "authenticated user only"; it is "scoped attachment ownership." Anonymous sessions still need workspace, workflow, thread, and session scope.

### Product goals and Non-goals

**Goals**
- Preserve unauthenticated uploads for public workflows.
- Replace `storage_path` exposure with opaque `attachment_id` references.
- Store attachment bytes once in a dedicated blob table by default.
- Keep workflow and LangGraph checkpoint state small by storing references, not payload bytes.
- Enforce attachment reads through an attachment service that checks workspace/workflow/thread/session scope.
- Remove filesystem writes from ChatKit workflow runs.

**Non-goals**
- Building a generic file manager or downloadable attachment library.
- Supporting arbitrary filesystem reads/writes from workflow code.
- Migrating all deployments to object storage.
- Adding malware scanning, OCR, PDF parsing, image processing, or long-term retention policy changes in the MVP.
- Solving every abuse-control problem for public uploads beyond the existing upload constraints and rate limits.

## PRODUCT DEFINITION

### Requirements

#### P0: Secure DB-backed attachment MVP
- ChatKit upload responses must return opaque attachment references and must not return server filesystem paths.
- Uploaded bytes must be persisted in a dedicated Postgres blob table by default.
- Attachment metadata must include at least:
  - `attachment_id`
  - `workspace_id`
  - `workflow_id`
  - `thread_id` and/or an anonymous/public `upload_session_id`
  - original/sanitized display name
  - MIME type
  - size in bytes
  - SHA-256 digest
  - storage backend identifier
  - creation time
- New uploads must be rejected if the backend cannot resolve a workspace, workflow, and at least one thread/session binding before persistence.
- Workflow inputs must carry attachment references, not raw file bytes.
- `DocumentLoaderNode` or its adapter must resolve attachment references through a scoped resolver supplied by the ChatKit workflow runtime.
- Core `orcheo` nodes must not import the backend package directly; the backend owns the attachment service and injects a resolver/protocol into workflow execution.
- Reads must verify server-trusted workspace/workflow/thread/session scope before returning bytes or decoded content. Scope values embedded in document payloads are metadata only and must not be treated as authority.
- Existing `storage_path`-based ChatKit inputs must be removed from new upload and workflow paths.
- Upload size and type validation must remain enforced before persistence.
- Cleanup/pruning must delete attachment metadata and blob payloads together, preferably through one transactional service operation for the Postgres backend.

#### P1: Object/blob storage delegation extension
- Add a configurable attachment blob backend that delegates payload bytes to S3-compatible storage, R2, MinIO, or a future provider.
- The Postgres blob table remains the default when no backend is explicitly configured.
- Delegated blob storage must preserve the same metadata contract, scope checks, attachment ids, pruning semantics, and workflow input shape.
- Object keys must be private implementation details and must never be accepted from workflow state as read authority.

#### P2: Hardening and operations
- Add configurable retention by workspace or public workflow.
- Prune orphaned upload sessions (rows with `upload_session_id` set, `thread_id IS NULL`, and `created_at` older than a configurable cutoff).
- Add optional malware scanning or content classification.
- Add attachment access audit events.
- Add deduplication by SHA-256 when it is worth the complexity.
- Add scoped temporary files only for libraries that require paths, with lifecycle management and no caller-controlled paths.

### Designs
See [2_design.md](2_design.md).

### Other Teams Impacted
- Backend: owns upload route, attachment store, workflow execution input shape, and `DocumentLoaderNode` integration.
- Studio/Public Chat: must send `workflow_id` (and `thread_id` or `upload_session_id` when available) in direct-upload requests, and must stop reading `storage_path` from upload responses or attachment metadata.
- Infrastructure/SRE: owns optional object storage configuration and capacity planning.
- Security: reviews scope checks, anonymous session binding, and filesystem removal.

### Current State Observations
- `/api/chatkit/upload` currently takes only `file` and `request` — no `workflow_id`, `thread_id`, or upload-session inputs — and constructs a minimal context with hardcoded `auth_mode: "publish"` and empty `workflow_id`. Adopting the same public/JWT authorization rules as ChatKit message invocation is a behavioral change, not a refactor.
- `chat_attachments` already has a `storage_path` column. The new scoped columns (`workspace_id`, `workflow_id`, `upload_session_id`, `auth_mode`, `blob_backend`, `sha256`, `size_bytes`, …) must be added through additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements on the existing table, not by recreating it.
- `_infer_thread_id` reads `chatkit_request.params.thread_id`, but at upload time the route passes `chatkit_request: None`, so today every uploaded attachment is persisted with `thread_id = NULL` and no thread linkage.

## TECHNICAL CONSIDERATIONS

### Architecture Overview
The attachment service becomes the authority for uploaded file access. ChatKit upload persists metadata plus bytes, returns `attachment_id`, and workflow execution passes that id through state. Nodes resolve attachment ids through a runtime-provided resolver that calls the backend attachment reader with the request's workspace/workflow/thread/session context before returning content.

LangGraph persistence remains responsible for workflow state, not file payload storage. State should contain small references so checkpoints do not repeatedly retain large bytes.

### Technical Requirements
- Postgres schema migration or additive `ensure_schema` update adds an attachment blob table, required scope columns, and indexes. The implementation must account for the repository's current schema-bootstrap pattern.
- Existing attachment metadata lookup must stop querying by id alone; it must include scope predicates.
- Upload route must resolve workflow/workspace/public session context before saving and must use the same public/JWT authorization semantics as ChatKit message invocation.
- `DocumentLoaderNode` must support `attachment_id` as a first-class document input through an injected resolver, without taking a dependency on backend internals.
- Legacy `storage_path` reads must be removed from ChatKit-generated document payloads and eventually deprecated from generic document inputs.
- Tests must cover cross-workspace/thread/session denial cases.

### AI/ML Considerations

#### Data Requirements
Uploaded files may feed RAG and document ingestion nodes. The feature does not change embedding or retrieval semantics; it changes only attachment transport and access control.

#### Algorithm selection
No model or algorithm changes.

#### Model performance requirements
No direct model performance requirements. Indirectly, workflows should not see meaningful latency regression for small text attachments.

## LAUNCH/ROLLOUT PLAN

### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| Primary: Cross-scope attachment access | 0 successful unauthorized reads in automated tests and security review |
| Secondary: Upload compatibility | Existing supported ChatKit text upload flows pass with attachment ids |
| Guardrail: DB growth | Attachment storage growth is observable by table and workspace before rollout |
| Guardrail: Latency | Small text upload and first read remain within current p95 plus acceptable DB write overhead |

### Rollout Strategy
Ship behind a backend configuration flag that defaults to the new DB blob implementation in development and test. For production, provide a migration playbook that enables the new path for new uploads first, then disables filesystem-backed ChatKit upload once compatibility is verified.

### Estimated Launch Phases
| Phase | Target | Description |
|-------|--------|-------------|
| Phase 1 | Development/test | Add schema, service, upload path, and node resolution with compatibility tests |
| Phase 2 | Staging | Enable DB blob backend for new uploads, verify public workflow uploads and pruning |
| Phase 3 | Production | Enable DB blob backend by default and stop returning `storage_path` |
| Phase 4 | Optional extension | Enable delegated blob storage only for deployments that explicitly configure it |

## HYPOTHESIS & RISKS

**Hypothesis:** Storing attachment bytes once in a dedicated blob table and carrying only attachment references through LangGraph state removes the current cross-workspace path risk without requiring authenticated public users. Confidence is high because the design shifts authorization from filesystem paths to scoped metadata checks.

**Risk: Database bloat.** Large or repeated uploads can grow Postgres storage, WAL, backups, and replication load. Mitigation: enforce size limits, keep payloads out of checkpoints, add per-workspace observability, and provide delegated blob storage as an explicit extension.

**Risk: Throughput regression.** DB blob writes are heavier than local filesystem writes. Mitigation: keep MVP limits conservative, stream reads where possible, avoid duplicate checkpoint writes, and document when deployments should enable delegated blob storage.

**Risk: Anonymous session ambiguity.** Public visitors may not have authenticated user ids. Mitigation: bind uploads to workflow/workspace plus thread/session identifiers and require those identifiers for later reads.

**Risk: Legacy workflows depend on `storage_path`.** Mitigation: remove `storage_path` from ChatKit-generated payloads first, document the deprecation, and treat generic filesystem document loading as a separate sandboxed feature.

**Risk: Backend/core dependency leak.** `DocumentLoaderNode` is part of the core package, but the attachment service belongs to the backend. Mitigation: define a small resolver protocol or callable contract that the backend injects through workflow execution config.

## APPENDIX

### Agreed Design Principles
- Public workflows may accept unauthenticated uploads.
- Unauthenticated does not mean unscoped.
- LangGraph state should carry attachment references, not attachment bytes.
- Postgres blob table is the default storage backend.
- Object storage delegation is an extension enabled only when configured.
- Filesystem read/write support is a separate future feature, not part of ChatKit upload handling.
