# Design Document

## For Candidate Workflow Version Updates

- **Version:** 0.1
- **Author:** Codex
- **Date:** 2026-06-23
- **Status:** Draft

---

## Overview

This initiative gives candidate colleagues an explicit release lifecycle. Candidate workflow files declare a strict SemVer `version` and optional versioned `updates` entries in the existing `# /// orcheo` frontmatter block. The backend candidate service parses and exposes that release metadata through `/api/candidates`.

When a user onboards a candidate, Orcheo records which candidate id/handle/version produced the colleague's workflow version. Studio can then compare that installed candidate version against the latest candidate fetched from the repository and show an update action only when the latest candidate version is greater.

The update operation remains explicit. Hover/focus on the update button gives compact context; clicking opens a confirmation dialog with full update notes for the versions being crossed. Confirming appends a new workflow version from the latest candidate script/config and preserves the colleague's workflow identity, team, credentials, workspace bindings, and history.

## Components

- **Workflow Frontmatter Parser (`packages/sdk`)**
  - Adds `version` and `updates` to the allowed `orcheo` frontmatter fields.
  - Validates strict SemVer strings and update-note structure.

- **Candidate Service (`apps/backend`)**
  - Parses candidate version/update notes from the candidates repository.
  - Exposes release metadata through internal and public candidate schemas.
  - Preserves stale-while-revalidate cache semantics.

- **Candidate Router (`apps/backend`)**
  - Records source candidate metadata when onboarding.
  - Adds an explicit update endpoint for existing onboarded colleagues.
  - Reuses script ingestion, runnable config normalization, plugin checks, and Mermaid rendering from onboarding.

- **Workflow Repository (`apps/backend`)**
  - Persists source candidate metadata on workflow version metadata, or in a dedicated nullable source metadata field if introduced.
  - Lists workflow versions with enough metadata for update detection.

- **Studio API Client (`apps/studio`)**
  - Extends candidate and workflow version types with candidate version/update metadata.
  - Calls the update endpoint and refreshes workflow/gallery state after success.

- **Studio Gallery UI (`apps/studio`)**
  - Shows an update button on onboarded colleague cards when an update is available.
  - Shows compact update notes on hover/focus.
  - Opens a review/confirmation dialog before applying the update.

## Request Flows

### Flow 1: Candidate Release Metadata Parsing

1. Candidate author updates `colleagues/<id>/workflow.py` frontmatter with `version` and `updates`.
2. Backend candidate service downloads the candidate repository tarball.
3. Candidate parser reads `workflow.py`, validates frontmatter, and builds `CandidateItem`.
4. `/api/candidates` returns public candidate metadata including version and update notes, but still omits script/config.

### Flow 2: Onboard Candidate with Source Version

1. User clicks Onboard on a candidate card.
2. Studio posts candidate id and optional team id to `POST /api/candidates/onboard`.
3. Backend fetches the candidate from the server-side cache.
4. Backend ingests the script and creates a workflow/version as today.
5. Workflow version metadata includes `candidate_id`, `candidate_handle`, and `candidate_version`.
6. Studio refreshes the gallery and the new colleague is pinned to the installed candidate version.

### Flow 3: Detect Update Availability

1. Studio loads workspace workflows and candidates.
2. For each onboarded colleague, Studio reads latest workflow version metadata.
3. If metadata identifies a candidate source and both versions are valid SemVer, Studio compares installed and latest candidate versions.
4. If latest is greater, the card shows an update action with hover/focus details.

Server-side detection is acceptable if implemented as an enriched workflow/gallery response; the comparison rules must remain the same.

### Flow 4: Review and Apply Update

1. User hovers or focuses the update button and sees a short summary.
2. User clicks the update button.
3. Studio opens a confirmation dialog showing:
   - current installed candidate version
   - latest candidate version
   - update notes for versions greater than installed and less than or equal to latest
   - migration guidance and major-update warning when applicable
4. User confirms.
5. Studio calls update endpoint with workflow id and candidate id.
6. Backend validates that the workflow is an onboarded colleague from the requested candidate.
7. Backend ingests latest candidate script/config and appends a new workflow version.
8. Backend returns the updated workflow; Studio refreshes gallery/workflow state.

## API Contracts

Existing candidate list response expands with release metadata:

```text
GET /api/candidates

Response:
  200 OK -> [
    {
      id: string,
      handle: string,
      name: string,
      description: string | null,
      avatar: string | null,
      subtitle: string | null,
      notes: string | null,
      metadata: object | null,
      mermaid: string | null,
      version: string | null,
      updates: CandidateUpdateNote[]
    }
  ]
```

Candidate update endpoint:

```text
POST /api/candidates/update
Headers:
  X-Orcheo-Workspace: <workspace>
Body:
  workflow_id: string
  candidate_id: string

Response:
  200 OK -> Workflow
  400 -> candidate is unversioned, script ingestion failed, or plugins/config invalid
  403 -> user cannot update the workflow in this workspace
  404 -> workflow or candidate not found
  409 -> workflow is not sourced from this candidate, or latest version is not newer
```

An equivalent nested route is also acceptable:

```text
POST /api/workflows/{workflow_id}/candidate-update
Body:
  candidate_id: string
```

The endpoint returns `409` with code `candidate.no_update_available` for already-current colleagues and performs no mutation.

## Data Models / Schemas

### Frontmatter

```toml
version = "1.3.0"

[[updates]]
version = "1.3.0"
summary = "Adds source-quality checks before insight synthesis."
migration = "Review custom prompt overrides if they assume every source is accepted."
```

### Candidate Update Note

| Field | Type | Description |
|-------|------|-------------|
| `version` | string | Strict SemVer `x.y.z` release version |
| `summary` | string | Short user-facing change summary |
| `migration` | string \| null | Optional migration guide, warning, or operator action |

### Candidate API Model Additions

| Field | Type | Description |
|-------|------|-------------|
| `version` | string \| null | Latest candidate release version parsed from workflow frontmatter |
| `updates` | list[CandidateUpdateNote] | Versioned update notes, sorted newest first |

### Workflow Version Source Metadata

Persisted in workflow version metadata:

```json
{
  "source": "candidate-onboard",
  "candidate_id": "insight-analyst",
  "candidate_handle": "insight-analyst",
  "candidate_version": "1.3.0",
  "candidate_source_ref": "main"
}
```

If `candidate_source_ref` can be made more precise than the configured ref, prefer the fetched Git commit SHA. If not available from the tarball request in v1, store the configured repo ref and document that it is a cache-source hint, not a content hash.

### SemVer Rules

Strict v1 format:

```text
MAJOR.MINOR.PATCH
```

Rules:
- Each segment is a base-10 non-negative integer.
- No empty segments.
- No leading `v`.
- No prerelease suffix.
- No build metadata suffix.
- Numeric comparison is segment-wise: major, then minor, then patch.

## Security Considerations

- Candidate scripts remain server-sourced from the configured candidate repository; clients cannot submit script/config through the update endpoint.
- Preserve existing workspace authorization and the hard-coded `X-Orcheo-Workspace` scope.
- Do not trust client-provided current/latest versions. Backend must resolve candidate and workflow metadata server-side before mutating.
- Continue plugin availability checks before onboarding/updating candidates.
- Update notes are repository-authored text and must be rendered as plain text or sanitized Markdown in Studio.
- Candidate configs must continue to validate through `RunnableConfigModel`; secrets should not be embedded in candidate config.

## Performance Considerations

- SemVer comparison and update-note filtering are negligible.
- Candidate cache behavior remains TTL-based with stale-while-revalidate; no per-card GitHub calls.
- Studio should memoize update-availability calculations by `{workflow latest version id, candidate id, candidate version}`.
- Update operation cost is similar to re-onboarding: script ingestion plus version creation.

## Testing Strategy

- **Unit tests**
  - Frontmatter parser accepts valid `version` and `updates`.
  - Frontmatter parser rejects invalid SemVer and malformed update notes.
  - SemVer comparator handles major/minor/patch ordering.
  - Candidate parser exposes version/update metadata and preserves unversioned candidates.

- **Backend integration tests**
  - `/api/candidates` returns version/update metadata but still omits script/config.
  - Onboarding stores candidate source version metadata.
  - Update endpoint appends a new version when latest candidate is newer.
  - Update endpoint rejects wrong candidate id, missing candidate, invalid config, missing plugins, and already-current versions.
  - Failed update leaves prior latest version unchanged.

- **Studio tests**
  - Onboarded colleague card shows update action only when latest candidate version is greater.
  - Hover/focus content includes current/latest versions and concise update summary.
  - Confirmation dialog shows full notes for crossed versions and major-update warning.
  - Confirming update calls the API and refreshes cached gallery state.

- **Manual QA checklist**
  - Add a versioned candidate and onboard it.
  - Increase patch version and verify update button appears.
  - Hover update button and verify compact notes.
  - Confirm update and verify a new workflow version is appended.
  - Verify credentials, team placement, workflow handle, and execution history remain intact.
  - Try a major update and verify stronger warning copy.

## Rollout Plan

1. Phase 1: Add parser/schema support and tests; deploy with no Studio update UI yet.
2. Phase 2: Store source candidate version metadata on onboarding/update and expose enough workflow metadata for update detection.
3. Phase 3: Add Studio update action, hover/focus popover, confirmation dialog, and update API integration.
4. Phase 4: Document candidate authoring policy and migrate official candidates to versioned frontmatter.

Include backward compatibility for candidates and onboarded colleagues without candidate version metadata.

## Rollback Plan

- Hide the Studio update action if update behavior needs to be paused.
- Keep frontmatter parser additions if safe; unrecognized older clients can ignore new fields only after parser support is deployed.
- Existing workflow version metadata can remain in place without being used.
- Disable the update endpoint at the router level if needed; onboarding remains unchanged.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-06-23 | Codex | Initial draft |
