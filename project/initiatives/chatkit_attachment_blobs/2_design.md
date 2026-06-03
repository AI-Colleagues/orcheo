# Design Document

## For ChatKit Attachment Blob Storage

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-05-27
- **Status:** Draft

---

## Overview

This design replaces ChatKit's path-based file attachment handling with a scoped attachment service and database-backed blob storage. The immediate goal is to preserve public workflow uploads while removing shared server filesystem paths from the workflow execution trust model.

The default implementation stores uploaded bytes in Postgres in a dedicated blob table. LangGraph state and checkpoints carry only attachment references such as `attachment_id`, `name`, `mime_type`, and `size_bytes`. `DocumentLoaderNode` resolves attachment ids through an injected resolver that delegates to the backend attachment service, which validates workspace, workflow, thread, and anonymous session scope before returning content.

Object storage is an extension, not a migration requirement. If a deployment explicitly configures S3-compatible storage, R2, MinIO, or another provider, the attachment service can delegate payload bytes to that backend while the metadata table, scope checks, and workflow input contract remain the same. Deployments that do not configure object storage continue to use the Postgres blob table.

The core `orcheo` package must remain independent of backend internals. Attachment resolution should therefore be exposed to `DocumentLoaderNode` as a small resolver protocol/callable supplied by the ChatKit workflow runtime, not by importing `orcheo_backend` from core node code.

## Components

- **ChatKit upload route (`apps/backend/src/orcheo_backend/app/routers/chatkit.py`)**
  - Accepts direct uploads from the ChatKit composer for published/public and authenticated ChatKit sessions.
  - Accepts new multipart form fields: `workflow_id` (required), `thread_id` (optional), `upload_session_id` (optional).
  - Resolves workspace, workflow, thread, and session scope before persistence — replacing today's hardcoded `auth_mode: "publish"` and empty `workflow_id` context.
  - Uses the same public/JWT authorization rules as ChatKit message invocation.
  - Validates size, filename, MIME type, and text decoding rules.
  - Persists metadata and bytes through the attachment service.
  - Returns opaque attachment metadata without `storage_path`.

- **Attachment service (`apps/backend/src/orcheo_backend/app/chatkit/attachments.py` or equivalent)**
  - Owns `save_attachment`, `load_attachment_bytes`, `delete_attachment`, and pruning operations.
  - Enforces scope checks on every read.
  - Shields workflow code and nodes from storage backend details.
  - Provides a stable contract for DB blobs now and object storage delegation later.
  - Links upload-session-scoped attachments to a thread when the first ChatKit message creates or identifies the thread.

- **Postgres ChatKit store (`apps/backend/src/orcheo_backend/app/chatkit_store_postgres`)**
  - Persists attachment metadata with explicit scope columns.
  - Persists default blob payloads in a related `chat_attachment_blobs` table.
  - Keeps `details_json` for ChatKit-compatible metadata but does not rely on it for authorization.

- **Workflow input builder (`apps/backend/src/orcheo_backend/app/chatkit/messages.py`)**
  - Converts ChatKit attachment objects into document references.
  - Stops reconstructing filesystem paths from `CHATKIT_STORAGE_PATH`.
  - Emits `attachment_id`, display name, MIME type, and size metadata.

- **Document loading (`src/orcheo/nodes/rag/ingestion.py`)**
  - Supports `attachment_id` as a first-class raw document input.
  - Resolves attachment content through an injected or runtime attachment resolver supplied in `RunnableConfig` or an equivalent execution context.
  - Does not treat caller-provided filesystem paths as ChatKit attachment authority.
  - Does not import backend modules directly.

- **LangGraph persistence (`src/orcheo/persistence.py`)**
  - Continues to persist workflow state and checkpoints.
  - Stores only small attachment references in state; it does not store attachment bytes for ChatKit uploads.

- **Optional delegated blob backend**
  - Implements the same byte storage interface as the Postgres blob table.
  - Stores private object keys as implementation details in metadata.
  - Is enabled only when configured explicitly.

## Request Flows

### Flow 1: Public workflow upload

1. Public visitor opens a published ChatKit workflow and uploads a supported file.
2. Upload request includes enough ChatKit context to resolve `workflow_id`, workspace, and either thread id or anonymous/public upload session id. If the client cannot provide a thread id yet, the backend creates or records a temporary upload session id, returns it with the upload metadata when needed, and links it when the first message arrives.
3. Backend validates that the workflow is public and accepts unauthenticated ChatKit traffic.
4. Backend reads the upload with a configured size guard and validates filename/content type.
5. Attachment service creates an attachment metadata row and writes bytes to `chat_attachment_blobs` in one service operation.
6. Backend returns:

```json
{
  "id": "atc_abc123",
  "name": "notes.txt",
  "mime_type": "text/plain",
  "type": "file",
  "size": 1234,
  "sha256": "hex digest",
  "upload_session_id": "ups_abc123"
}
```

`upload_session_id` is present only when the backend minted or needs the client to echo a temporary upload session. No server filesystem path is returned.

### Flow 2: Workflow execution with attachment

1. ChatKit message arrives with one or more attachment ids.
2. The input builder emits:

```json
{
  "documents": [
    {
      "attachment_id": "atc_abc123",
      "source": "notes.txt",
      "metadata": {
        "mime_type": "text/plain",
        "size": 1234
      }
    }
  ]
}
```

3. Workflow execution builds initial LangGraph state with those references.
4. `DocumentLoaderNode` receives the document reference.
5. The node asks the injected attachment resolver for `atc_abc123`. The resolver receives server-trusted workspace/workflow/thread/session context from the ChatKit runtime, not from the document payload itself.
6. Resolver queries metadata with scope predicates, reads bytes from the configured backend, decodes content, and returns it to the node.
7. The node emits normalized document content in its normal output shape.

### Flow 3: Cross-scope read denial

1. A workflow run in workspace B receives or guesses an attachment id from workspace A.
2. `DocumentLoaderNode` asks the attachment resolver to load that id.
3. Resolver queries by `id`, `workspace_id`, and accepted workflow/thread/session scope.
4. No row matches, so the service returns a not-found or forbidden error without revealing whether the id exists elsewhere.

### Flow 4: Pruning

1. Retention job identifies expired ChatKit threads. Orphaned upload sessions are identified by the predicate `thread_id IS NULL AND linked_at IS NULL AND created_at < (now() - orphan_cutoff)` where `orphan_cutoff` defaults to a short window (e.g., 24h) and is configurable.
2. Attachment service selects associated attachment metadata rows.
3. Blob payloads are deleted from `chat_attachment_blobs` or delegated storage.
4. Metadata rows are deleted.
5. Failures are logged with attachment id and backend type, not raw object paths exposed to workflow code.

### Flow 5: Delegated blob storage extension

1. Operator configures `CHATKIT_ATTACHMENT_BLOB_BACKEND=s3` or another supported backend and provides provider settings.
2. Upload route and node resolver continue to call the attachment service.
3. Attachment service stores metadata in Postgres and bytes in the delegated backend.
4. Metadata records include `blob_backend`, `blob_key`, size, and digest.
5. Reads still require metadata scope checks before any backend object fetch.

## API Contracts

### Upload

```
POST /api/chatkit/upload
Headers:
  X-Orcheo-Workspace: <workspace>    # when available
Body:
  multipart/form-data file=<upload>
  workflow_id=<uuid>
  thread_id=<string, optional>
  upload_session_id=<string, optional>

Response:
  200 OK -> {
    "id": "atc_abc123",
    "name": "notes.txt",
    "mime_type": "text/plain",
    "type": "file",
    "size": 1234,
    "sha256": "...",
    "upload_session_id": "ups_abc123"
  }
  400 -> invalid filename/type/encoding
  403 -> workflow not public or session not authorized
  413 -> upload too large
  429 -> rate limited
```

The exact route may remain hidden from OpenAPI if it is an internal ChatKit direct-upload contract.

The backend must reject upload requests that cannot be authorized for the resolved workflow or cannot be bound to a thread/upload session scope.
`upload_session_id` is omitted unless the backend minted one or requires the client to echo it in the later ChatKit message path.

### Internal attachment resolver

The backend exposes a small Protocol that lives in core (`orcheo`) so nodes can depend on it without importing `orcheo_backend`:

```python
# src/orcheo/runtime/attachments.py (new module in core)
from typing import Protocol, runtime_checkable

class AttachmentScope(Protocol):
    workspace_id: str
    workflow_id: str | None
    thread_id: str | None
    upload_session_id: str | None

class AttachmentPayload(Protocol):
    id: str
    name: str
    mime_type: str
    size_bytes: int
    sha256: str
    content: bytes
    metadata: dict

@runtime_checkable
class AttachmentResolver(Protocol):
    async def load_attachment_bytes(
        self, attachment_id: str, scope: AttachmentScope
    ) -> AttachmentPayload: ...
```

The backend implements `AttachmentResolver` and injects an instance through `RunnableConfig["configurable"]["attachment_resolver"]` (alongside the trusted `AttachmentScope` under `configurable["attachment_scope"]`). `DocumentLoaderNode` pulls both from its `RunnableConfig` at execution time and never accepts scope or resolver references from workflow state or document payloads.

The important contract is that scope is supplied by the runtime, not by the untrusted document reference.

`AttachmentPayload`:

| Field | Type | Description |
|-------|------|-------------|
| id | string | Attachment id |
| name | string | Sanitized display name |
| mime_type | string | MIME type captured at upload |
| size_bytes | integer | Stored byte size |
| sha256 | string | Digest of stored bytes |
| content | bytes | Attachment payload |
| metadata | dict | Non-authoritative metadata for downstream use |

## Data Models / Schemas

### `chat_attachments`

| Field | Type | Description |
|-------|------|-------------|
| id | text primary key | Opaque attachment id, for example `atc_<random>` |
| workspace_id | text not null | Owning workspace |
| workflow_id | text not null for new rows | Workflow that accepted the upload |
| thread_id | text null | ChatKit thread, when known |
| upload_session_id | text null | Anonymous/public upload session, used before thread binding |
| auth_mode | text not null | `jwt` or `publish`, captured for diagnostics and retention policy |
| actor_subject | text null | Authenticated subject or public actor identifier when available |
| attachment_type | text not null | ChatKit attachment type, usually `file` or `image` |
| name | text not null | Sanitized display filename |
| mime_type | text not null | Client MIME type after server validation |
| size_bytes | bigint not null | Byte length |
| sha256 | text not null | Digest for integrity and future dedupe |
| details_json | jsonb not null | ChatKit-compatible details; not authorization source |
| blob_backend | text not null | `postgres` by default; extension values such as `s3` |
| blob_key | text null | Private backend reference for delegated storage; for Postgres this can be null or equal to attachment id |
| storage_path | text null | Existing legacy column only; never set for new blob-backed uploads |
| created_at | timestamptz not null | Creation time |
| linked_at | timestamptz null | When an upload session is bound to a thread |

Indexes:
- Primary key on `id`.
- `(workspace_id, id)`.
- `(workspace_id, workflow_id, thread_id)`.
- `(workspace_id, upload_session_id)`.
- `(workspace_id, created_at)`.
- Optional unique or dedupe index on `(workspace_id, sha256, size_bytes)` after dedupe is implemented.

Constraints:
- New rows must have either `thread_id` or `upload_session_id`.
- New rows must have `workflow_id`, `workspace_id`, `blob_backend`, `size_bytes`, and `sha256`.
- Legacy rows may temporarily violate the new non-null shape during migration, but the service must treat them as read-only compatibility records.

Migration semantics:
- The repository bootstraps the chatkit store via `ensure_schema`, which only emits `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`. Adding columns to the existing `chat_attachments` table requires explicit `ALTER TABLE chat_attachments ADD COLUMN IF NOT EXISTS …` statements appended to `ensure_schema`, one per new column.
- Non-null constraints on new columns must be enforced in the service layer until legacy rows are backfilled or aged out — schema-level `NOT NULL` would break existing deployments at startup.
- The new `chat_attachment_blobs` table can be created with `CREATE TABLE IF NOT EXISTS` and a `FOREIGN KEY (attachment_id) REFERENCES chat_attachments(id) ON DELETE CASCADE`.

### `chat_attachment_blobs`

| Field | Type | Description |
|-------|------|-------------|
| attachment_id | text primary key | One-to-one FK to `chat_attachments.id` with `ON DELETE CASCADE` |
| content | bytea not null | Stored upload bytes |
| size_bytes | bigint not null | Byte length |
| sha256 | text not null | Digest for integrity checks |
| created_at | timestamptz not null | Creation time |

The MVP can keep one blob row per attachment even when two uploads have identical content. Dedupe is deferred until storage pressure justifies the extra reference-counting and deletion semantics.

### Workflow document reference

```json
{
  "attachment_id": "atc_abc123",
  "source": "notes.txt",
  "metadata": {
    "mime_type": "text/plain",
    "size": 1234,
    "sha256": "..."
  }
}
```

The reference is safe to checkpoint because it is small and non-authoritative. Authorization is checked again at read time.

## Security Considerations

- **Anonymous but scoped:** Public uploads do not require a logged-in user, but every upload must still bind to workspace, workflow, and thread/session scope.
- **No path authority:** Workflow state, ChatKit metadata, and node inputs must not contain server filesystem paths as read authority.
- **Read checks:** Attachment loads query with scope predicates. Id-only lookup is not sufficient.
- **Trusted scope source:** Resolver scope comes from the authenticated ChatKit request/thread runtime. Fields inside workflow input documents are not trusted authorization inputs.
- **Opaque ids:** Attachment ids must be random enough to prevent guessing. Scope checks remain mandatory even if ids are high entropy.
- **Upload session ids:** Backend-minted upload session ids must be high entropy and scoped to workflow/workspace. They are a binding handle, not sufficient authorization by themselves.
- **Input validation:** Preserve maximum upload size, filename sanitization, MIME/content validation, and rate limiting.
- **Backend object keys:** Delegated storage keys are private implementation details. A workflow cannot provide a key and request a read.
- **Error behavior:** Cross-scope misses should return a generic not-found/forbidden error without confirming whether an attachment exists elsewhere.
- **Temporary files:** If a future parser requires a path, the attachment service may create scoped temporary files owned by the current run and delete them immediately after use. That is not a general filesystem feature.

## Performance Considerations

- Postgres blob storage increases DB size, WAL volume, backup size, and replication traffic. Keep upload limits conservative for the MVP.
- Store bytes once in the attachment blob table. Do not copy bytes into LangGraph state, execution history, `messages`, `results`, or checkpoint metadata.
- `DocumentLoaderNode` should stream or read once per workflow run. Avoid repeatedly resolving the same attachment in multiple nodes unless the workflow explicitly does so.
- Add observability for total blob bytes by workspace, upload count, failed reads, and p95 upload/read latency.
- Delegated object storage should be enabled for deployments with high upload volume, large files, or DB backup pressure.

## Testing Strategy

- **Unit tests**
  - Filename sanitization and upload validation.
  - Attachment metadata serialization without `storage_path`.
  - Scope predicate construction for load/delete.
  - `DocumentLoaderNode` handling of `attachment_id`.

- **Integration tests**
  - Public workflow upload creates metadata and blob rows.
  - Workflow input builder emits attachment references.
  - Document loading resolves bytes through the attachment service.
  - Cross-workspace, cross-workflow, cross-thread, and wrong-session reads fail.
  - Pruning deletes metadata and blob rows.

- **Regression tests**
  - Upload response does not include `storage_path`.
  - ChatKit-generated documents do not include `storage_path`.
  - LangGraph initial state contains references only, not raw bytes.

- **Manual QA checklist**
  - Published public workflow accepts a text upload and uses it in a RAG/document workflow.
  - Unpublished workflow upload is rejected.
  - Anonymous session cannot reuse another session's attachment id.
  - Existing non-upload ChatKit message flow still works.

## Rollout Plan

1. Add schema and attachment service behind a feature flag.
2. Add DB blob backend and keep existing filesystem path as a temporary fallback only for old rows. Any fallback must first load a legacy metadata row by scoped predicates, verify the path remains under the configured legacy upload root, and never accept a caller-provided path as authority.
3. Update upload route to write DB blobs and return attachment references.
4. Update input builder and `DocumentLoaderNode` to resolve `attachment_id`.
5. Enable in development/staging and run cross-scope tests.
6. Enable for new production uploads.
7. Stop emitting and accepting ChatKit `storage_path` for new attachments.
8. Add optional delegated blob backend after DB blob behavior is stable.

Backwards compatibility: existing rows with `storage_path` may need a one-time migration, a read-only compatibility path, or explicit expiration. New uploads should never create filesystem-backed rows once the feature is enabled.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-27 | Codex | Initial draft |
| 2026-05-27 | Codex | Clarified resolver injection, scoped upload binding, blob schema, and legacy path handling |
| 2026-05-27 | Claude (review) | Added concrete `AttachmentResolver` Protocol, ALTER-TABLE migration semantics, upload-session orphan predicate, and current-state notes on upload route auth shape |
