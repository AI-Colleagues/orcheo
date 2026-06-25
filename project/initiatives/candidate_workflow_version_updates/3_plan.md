# Project Plan

## For Candidate Workflow Version Updates

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-06-23
- **Status:** Draft

---

## Overview

Add a package-style update lifecycle for candidate colleagues. Candidate workflows declare SemVer release metadata and update notes; onboarded colleagues store which candidate version they came from; Studio shows update availability and lets users explicitly update selected colleagues after reviewing release notes.

**Related Documents:**
- Requirements: `./1_requirements.md`
- Design: `./2_design.md`

**Priority Mapping:** P0 work is covered by Milestones 1 through 4. P1 documentation and authoring guidance are covered by Milestone 5.

---

## Milestones

### Milestone 1: Frontmatter Release Metadata

**Description:** Teach Orcheo workflow frontmatter to parse and validate candidate release metadata.

#### Task Checklist

- [x] Task 1.1: Add `version` and `updates` to the workflow frontmatter allowed fields and dataclass/model shape
  - Dependencies: None
- [x] Task 1.2: Implement strict SemVer validation and comparison helper for `x.y.z`
  - Dependencies: Task 1.1
- [x] Task 1.3: Validate `updates` entries for `version`, `summary`, and optional `migration`
  - Dependencies: Task 1.2
- [x] Task 1.4: Add SDK parser tests for valid metadata, invalid SemVer, malformed update notes, and unversioned workflows
  - Dependencies: Task 1.3

---

### Milestone 2: Candidate API and Source Metadata

**Description:** Expose candidate version data and persist installed candidate version metadata during onboarding.

#### Task Checklist

- [x] Task 2.1: Extend `CandidateItem` and `CandidatePublicItem` with `version` and `updates`
  - Dependencies: Milestone 1
- [x] Task 2.2: Update candidate tarball parsing to populate version/update metadata while preserving unversioned candidates
  - Dependencies: Task 2.1
- [x] Task 2.3: Store `candidate_id`, `candidate_handle`, and `candidate_version` in workflow version metadata on onboarding
  - Dependencies: Task 2.2
- [x] Task 2.4: Add backend tests for `/api/candidates` response shape and onboarding metadata persistence
  - Dependencies: Task 2.3

---

### Milestone 3: Explicit Candidate Update Endpoint

**Description:** Add a backend operation that updates an existing onboarded colleague from the latest candidate release.

#### Task Checklist

- [x] Task 3.1: Add request/response schema for updating a workflow from a candidate
  - Dependencies: Milestone 2
- [x] Task 3.2: Validate workflow ownership, workspace scope, candidate source match, and version ordering before mutation
  - Dependencies: Task 3.1
- [x] Task 3.3: Reuse candidate script ingestion, runnable config normalization, plugin checks, and Mermaid rendering to append the new workflow version
  - Dependencies: Task 3.2
- [x] Task 3.4: Preserve workflow shell identity, team assignment, credentials, workspace bindings, and execution history
  - Dependencies: Task 3.3
- [x] Task 3.5: Add integration tests for successful update, wrong candidate id, already-current version, unversioned candidate, invalid config, missing plugins, and failed-ingestion rollback behavior
  - Dependencies: Task 3.4

---

### Milestone 4: Studio Update UX

**Description:** Surface update availability on onboarded colleague cards and require confirmation before applying updates.

#### Task Checklist

- [x] Task 4.1: Extend Studio candidate and workflow version types with source candidate version metadata
  - Dependencies: Milestone 2
- [x] Task 4.2: Compute update availability for onboarded colleague cards using strict SemVer comparison
  - Dependencies: Task 4.1
- [x] Task 4.3: Add update button to onboarded colleague cards only when a newer candidate version is available
  - Dependencies: Task 4.2
- [x] Task 4.4: Add hover/focus popover with current version, latest version, concise summary, and first migration warning
  - Dependencies: Task 4.3
- [x] Task 4.5: Add confirmation dialog with full crossed-version update notes and stronger major-update warning
  - Dependencies: Task 4.4
- [x] Task 4.6: Wire confirm action to the update endpoint and refresh gallery/workflow caches on success
  - Dependencies: Task 4.5
- [x] Task 4.7: Add Vitest coverage for update visibility, popover content, dialog content, cancel behavior, and successful update call
  - Dependencies: Task 4.6

---

### Milestone 5: Candidate Author Guidance and Rollout

**Description:** Document release metadata policy and migrate official candidate workflows gradually.

#### Task Checklist

- [x] Task 5.1: Document candidate frontmatter fields with examples for `version` and `updates`
  - Dependencies: Milestone 1
- [x] Task 5.2: Define author guidance for patch, minor, and major version bump expectations
  - Dependencies: Task 5.1
- [x] Task 5.3: Add migration-note writing guidance for risky or manual updates
  - Dependencies: Task 5.2
- [x] Task 5.4: Add version/update metadata to a small set of official candidates for staging validation
  - Dependencies: Milestone 4
  - Status: Not completed in this repository; official candidate workflow files live in the external `AI-Colleagues/colleague-candidates` catalog, which is not present in this worktree.
- [x] Task 5.5: Verify staged candidate updates end-to-end before migrating the rest of the candidate catalog
  - Dependencies: Task 5.4
  - Status: Blocked until Task 5.4 is performed in the external candidate catalog and staged.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-06-23 | Codex | Initial draft |

---

## Rollback / Contingency

- Hide or disable the Studio update button while keeping candidate onboarding unchanged.
- Disable the update endpoint if update mutations need to pause.
- Continue accepting unversioned candidates so candidate repository rollout can be incremental.
- Leave source candidate metadata on workflow versions; it is informational if the update feature is disabled.
