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

- [ ] Task 1.1: Add Postgres schema migration for `chat_attachment_blobs` and new scoped columns on `chat_attachments`.
  - Dependencies: None
- [ ] Task 1.2: Add indexes for `(workspace_id, id)`, `(workspace_id, workflow_id, thread_id)`, `(workspace_id, upload_session_id)`, and `(workspace_id, created_at)`.
  - Dependencies: Task 1.1
- [ ] Task 1.3: Define an internal attachment service interface for save, scoped read, delete, and prune.
  - Dependencies: None
- [ ] Task 1.4: Implement the default Postgres blob backend with byte size and SHA-256 verification.
  - Dependencies: Task 1.1, Task 1.3
- [ ] Task 1.5: Add unit tests for schema helpers, serialization, hashing, and scoped lookup predicates.
  - Dependencies: Task 1.3, Task 1.4

---

### Milestone 2: Upload Path Migration

**Description:** Update ChatKit upload handling so new uploads persist bytes in the blob table and return opaque attachment metadata.

#### Task Checklist

- [ ] Task 2.1: Resolve workspace/workflow/thread/upload-session context in `/api/chatkit/upload`.
  - Dependencies: Milestone 1
- [ ] Task 2.2: Preserve existing max-size, filename, MIME, and text-decoding validation before persistence.
  - Dependencies: None
- [ ] Task 2.3: Replace `storage_path.write_bytes(...)` with attachment service persistence.
  - Dependencies: Task 2.1, Task 2.2
- [ ] Task 2.4: Remove `storage_path` from upload responses for new attachments.
  - Dependencies: Task 2.3
- [ ] Task 2.5: Add API tests for public workflow uploads, unpublished workflow rejection, oversized uploads, and response shape.
  - Dependencies: Task 2.4

---

### Milestone 3: Workflow Input and Document Loading

**Description:** Ensure workflow execution receives attachment references and resolves bytes only through the scoped attachment service.

#### Task Checklist

- [ ] Task 3.1: Update ChatKit input builder to emit `attachment_id` document references instead of reconstructed filesystem paths.
  - Dependencies: Milestone 2
- [ ] Task 3.2: Add `attachment_id` support to raw document input models.
  - Dependencies: Task 3.1
- [ ] Task 3.3: Add an attachment resolver path for `DocumentLoaderNode`.
  - Dependencies: Task 3.2
- [ ] Task 3.4: Ensure LangGraph initial state/checkpoints contain references only and no raw attachment bytes.
  - Dependencies: Task 3.1
- [ ] Task 3.5: Add integration tests for upload -> ChatKit message -> workflow input -> document loading.
  - Dependencies: Task 3.3

---

### Milestone 4: Security Regression Coverage

**Description:** Prove that attachment ownership is enforced by metadata scope rather than paths or ids alone.

#### Task Checklist

- [ ] Task 4.1: Add cross-workspace read-denial tests.
  - Dependencies: Milestone 3
- [ ] Task 4.2: Add cross-workflow read-denial tests within the same workspace.
  - Dependencies: Milestone 3
- [ ] Task 4.3: Add cross-thread or wrong anonymous-session read-denial tests.
  - Dependencies: Milestone 3
- [ ] Task 4.4: Add regression tests proving ChatKit-generated documents do not include `storage_path`.
  - Dependencies: Milestone 3
- [ ] Task 4.5: Add pruning tests that delete blob rows and metadata together.
  - Dependencies: Milestone 1

---

### Milestone 5: Compatibility and Rollout

**Description:** Enable the new storage path safely while deciding how to handle existing filesystem-backed attachments.

#### Task Checklist

- [ ] Task 5.1: Add configuration for the new attachment backend, with Postgres blob storage as the default.
  - Dependencies: Milestone 1
- [ ] Task 5.2: Decide and implement legacy `storage_path` handling: migrate existing rows, allow temporary read-only compatibility, or expire old attachments.
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

- [ ] Task 6.1: Define the backend selection config and provider-neutral blob interface.
  - Dependencies: Milestone 5
- [ ] Task 6.2: Implement an S3-compatible backend suitable for S3, R2, and MinIO.
  - Dependencies: Task 6.1
- [ ] Task 6.3: Store delegated object keys as private metadata and keep workflow state unchanged.
  - Dependencies: Task 6.2
- [ ] Task 6.4: Add integration tests proving scope checks happen before object reads.
  - Dependencies: Task 6.2
- [ ] Task 6.5: Document deployment examples for default Postgres, MinIO self-hosted, and cloud object storage.
  - Dependencies: Task 6.2

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-05-27 | Codex | Initial draft |
