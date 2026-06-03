# Project Plan

## For ChatKit Attachment Blob Storage

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-05-27
- **Status:** Draft

---

## Overview

Implement scoped, database-backed ChatKit attachment storage. The project removes shared filesystem writes from ChatKit uploads, keeps raw bytes out of LangGraph state, and introduces an attachment service that resolves opaque ids with workspace/workflow/thread/session checks.

**Related Documents:**
- Requirements: [1_requirements.md](1_requirements.md)
- Design: [2_design.md](2_design.md)

---

## Milestones

### Milestone 1: Schema and Service Contract

**Description:** Establish the persistent data model and attachment service interface without changing runtime behavior yet.

#### Task Checklist

- [x] Task 1.1: Extend `ensure_schema` in `apps/backend/src/orcheo_backend/app/chatkit_store_postgres/schema.py` with `CREATE TABLE IF NOT EXISTS chat_attachment_blobs (...)` and `ALTER TABLE chat_attachments ADD COLUMN IF NOT EXISTS …` statements for `workspace_id`, `workflow_id`, `upload_session_id`, `auth_mode`, `actor_subject`, `size_bytes`, `sha256`, `blob_backend`, `blob_key`, and `linked_at`. Keep new columns nullable at the schema level; enforce required-shape rules in the service layer so existing deployments do not fail on startup.
  - Dependencies: None
- [x] Task 1.2: Add indexes for `(workspace_id, id)`, `(workspace_id, workflow_id, thread_id)`, `(workspace_id, upload_session_id)`, and `(workspace_id, created_at)`, plus service guards rejecting new rows that lack workflow/workspace and thread/session scope.
  - Dependencies: Task 1.1
- [x] Task 1.3: Define an internal backend attachment service interface for save, scoped read, delete, prune, and upload-session-to-thread linking.
  - Dependencies: None
- [x] Task 1.4: Implement the default Postgres blob backend with one blob row per attachment, transactional metadata/blob writes, byte size checks, and SHA-256 verification.
  - Dependencies: Task 1.1, Task 1.3
- [x] Task 1.5: Add a core module (e.g., `src/orcheo/runtime/attachments.py`) defining `AttachmentScope`, `AttachmentPayload`, and the `AttachmentResolver` Protocol so `src/orcheo` can resolve attachments without importing `orcheo_backend`.
  - Dependencies: Task 1.3
- [x] Task 1.6: Add unit tests for schema helpers, serialization, hashing, scoped lookup predicates, and blob cleanup behavior.
  - Dependencies: Task 1.3, Task 1.4

---

### Milestone 2: Upload Path Migration

**Description:** Update ChatKit upload handling so new uploads persist bytes in the blob table and return opaque attachment metadata.

#### Task Checklist

- [x] Task 2.1: Update the `/api/chatkit/upload` route signature to accept `workflow_id` (required), `thread_id` (optional), and `upload_session_id` (optional) form fields, and replace the hardcoded `auth_mode: "publish"` + empty `workflow_id` context with the same public/JWT authorization semantics as ChatKit message invocation.
  - Dependencies: Milestone 1
- [x] Task 2.2: Preserve existing max-size, filename, MIME, and text-decoding validation before persistence.
  - Dependencies: None
- [x] Task 2.3: Reject uploads that cannot be bound to a thread id, client-provided `upload_session_id`, or backend-minted `upload_session_id`.
  - Dependencies: Task 2.1
- [x] Task 2.4: Replace `storage_path.write_bytes(...)` with attachment service persistence.
  - Dependencies: Task 2.1, Task 2.2
- [x] Task 2.5: Remove `storage_path` from upload responses for new attachments and return `upload_session_id` only when the client must echo it later.
  - Dependencies: Task 2.4
- [x] Task 2.6: Update the Canvas/public-chat composer client to send `workflow_id` (and `thread_id` or `upload_session_id` when available) on direct uploads, persist any backend-minted `upload_session_id`, and stop consuming `storage_path` from upload responses or attachment metadata.
  - Dependencies: Task 2.5
- [x] Task 2.7: Add API tests for public workflow uploads, JWT-scoped uploads, unpublished workflow rejection, oversized uploads, missing scope rejection, and response shape.
  - Dependencies: Task 2.5

---

### Milestone 3: Workflow Input and Document Loading

**Description:** Ensure workflow execution receives attachment references and resolves bytes only through the scoped attachment service.

#### Task Checklist

- [x] Task 3.1: Update ChatKit input builder to emit `attachment_id` document references instead of reconstructed filesystem paths.
  - Dependencies: Milestone 2
- [x] Task 3.2: Add `attachment_id` support to raw document input models without trusting document-supplied scope fields for authorization.
  - Dependencies: Task 3.1
- [x] Task 3.3: Add an injected attachment resolver path for `DocumentLoaderNode`, using `RunnableConfig` or an equivalent execution context instead of a backend import.
  - Dependencies: Task 3.2
- [x] Task 3.4: Pass trusted workspace/workflow/thread/session scope from the ChatKit workflow runtime to the resolver.
  - Dependencies: Task 3.3
- [x] Task 3.5: Link upload-session-scoped attachments to the ChatKit thread once the first message identifies the thread.
  - Dependencies: Task 3.1, Task 1.3
- [x] Task 3.6: Ensure LangGraph initial state/checkpoints contain references only and no raw attachment bytes.
  - Dependencies: Task 3.1
- [x] Task 3.7: Add integration tests for upload -> ChatKit message -> workflow input -> document loading.
  - Dependencies: Task 3.3, Task 3.4, Task 3.5

---

### Milestone 4: Security Regression Coverage

**Description:** Prove that attachment ownership is enforced by metadata scope rather than paths or ids alone.

#### Task Checklist

- [x] Task 4.1: Add cross-workspace read-denial tests.
  - Dependencies: Milestone 3
- [x] Task 4.2: Add cross-workflow read-denial tests within the same workspace.
  - Dependencies: Milestone 3
- [x] Task 4.3: Add cross-thread or wrong anonymous-session read-denial tests.
  - Dependencies: Milestone 3
- [x] Task 4.4: Add regression tests proving ChatKit-generated documents do not include `storage_path`.
  - Dependencies: Milestone 3
- [x] Task 4.5: Add pruning tests that delete blob rows and metadata together.
  - Dependencies: Milestone 1
- [x] Task 4.6: Add tests proving document payload metadata cannot override trusted resolver scope.
  - Dependencies: Milestone 3
- [x] Task 4.7: Add tests for orphaned upload-session pruning (rows with `thread_id IS NULL AND linked_at IS NULL AND created_at < cutoff`), including the linked-then-not-orphan case.
  - Dependencies: Milestone 1, Milestone 3

---

### Milestone 5: Compatibility and Rollout

**Description:** Enable the new storage path safely while deciding how to handle existing filesystem-backed attachments.

#### Task Checklist

- [x] Task 5.1: Add configuration for the new attachment backend, with Postgres blob storage as the default.
  - Dependencies: Milestone 1
- [x] Task 5.2: Decide and implement legacy `storage_path` handling: migrate existing rows, allow scoped read-only compatibility under the configured legacy upload root, or expire old attachments.
  - Dependencies: Milestone 3
- [ ] Task 5.3: Add metrics for upload count, blob bytes by workspace, read failures, and upload/read latency.
  - Dependencies: Milestone 2
- [ ] Task 5.4: Document operational guidance for DB growth, backup impact, and size-limit tuning.
  - Dependencies: Task 5.3
- [ ] Task 5.5: Enable the new path in staging, then production for new uploads.
  - Dependencies: Milestone 4, Task 5.1

---

### Milestone 6: Delegated Blob Storage Extension

**Description:** Add optional object/blob storage delegation for deployments that explicitly configure it, while keeping the Postgres blob table as the default backend.

#### Task Checklist

- [x] Task 6.1: Define the backend selection config and provider-neutral blob interface.
  - Dependencies: Milestone 5
- [x] Task 6.2: Implement an S3-compatible backend suitable for S3, R2, and MinIO.
  - Dependencies: Task 6.1
- [x] Task 6.3: Store delegated object keys as private metadata and keep workflow state unchanged.
  - Dependencies: Task 6.2
- [x] Task 6.4: Add integration tests proving scope checks happen before object reads.
  - Dependencies: Task 6.2
- [ ] Task 6.5: Document deployment examples for default Postgres, MinIO self-hosted, and cloud object storage.
  - Dependencies: Task 6.2

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-27 | Codex | Initial draft |
| 2026-05-27 | Codex | Added resolver-boundary, upload-session binding, scoped legacy fallback, and expanded security test tasks |
| 2026-05-27 | Claude (review) | Made schema task explicit about `ALTER TABLE`, added Canvas composer client task, added orphan upload-session pruning test, and tightened upload route task to add new form fields |
