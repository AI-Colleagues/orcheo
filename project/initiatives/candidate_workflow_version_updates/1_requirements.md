# Requirements Document

## METADATA
- **Authors:** Codex
- **Project/Feature Name:** Candidate Workflow Version Updates
- **Type:** Enhancement
- **Summary:** Add semantic versioning and versioned update notes to candidate workflow frontmatter, surface update availability for onboarded colleagues, and let users explicitly update selected colleagues to the latest candidate version.
- **Owner (if different than authors):** ShaojieJiang
- **Date Started:** 2026-06-23

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Prior Artifact | `project/initiatives/python_only_workflow_composition/1_requirements.md` | ShaojieJiang | Python-only workflow composition requirements |
| Prior Artifact | `project/initiatives/workflow_upload_config/1_requirements.md` | ShaojieJiang | Workflow upload runnable config requirements |
| Design Review | `./2_design.md` | ShaojieJiang | Candidate workflow version updates design |
| Project Plan | `./3_plan.md` | ShaojieJiang | Candidate workflow version updates plan |
| Candidate API | `apps/backend/src/orcheo_backend/app/routers/candidates.py` | ShaojieJiang | Candidate onboarding endpoint |
| Candidate Service | `apps/backend/src/orcheo_backend/app/candidates_service.py` | ShaojieJiang | Candidate repository parser and cache |
| Workflow Frontmatter Parser | `packages/sdk/src/orcheo_sdk/cli/workflow/frontmatter.py` | ShaojieJiang | Orcheo workflow frontmatter parsing |
| Studio Gallery | `apps/studio/src/features/workflow/pages/workflow-gallery/` | ShaojieJiang | Candidate and colleague gallery UI |

## PROBLEM DEFINITION
### Objectives
Make candidate colleague updates visible, understandable, and user-controlled. Each candidate workflow should declare a semantic release version and versioned update notes, while each onboarded colleague records the candidate version it was created or last updated from.

### Target users
- Studio users who onboard and operate candidate colleagues in a workspace.
- Workflow authors maintaining candidate workflows in the `colleague-candidates` repository.
- Platform engineers maintaining candidate ingestion, onboarding, and workflow version storage.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Candidate author | Declare a `version` in workflow frontmatter | Users and Orcheo can tell which candidate release is current | P0 | Candidate frontmatter accepts only SemVer `x.y.z`; invalid versions exclude the candidate with a logged parser warning |
| Candidate author | Add update notes and migration guidance to frontmatter | Users can understand whether they should update | P0 | Candidate metadata can include versioned update notes with summary and optional migration text |
| Studio user | See when an onboarded colleague has a newer candidate version | I can choose whether and when to update | P0 | Gallery cards for onboarded candidate colleagues show an update action when latest candidate SemVer is greater than the stored candidate version |
| Studio user | Hover over the update action | I can quickly understand what changed without opening a dialog | P0 | Hover/focus shows current version, latest version, and concise update summary; long notes are not forced into the hover content |
| Studio user | Click update and review details before applying | I can avoid accidental breaking changes | P0 | Clicking update opens a confirmation dialog with full notes for all versions being crossed and a clear update action |
| Studio user | Update one selected colleague | I can upgrade only the colleagues I choose | P0 | Confirming update appends a new workflow version from the latest candidate script/config and updates stored candidate source metadata |
| Platform engineer | Preserve workspace-specific colleague state during update | Updates do not erase local operational state | P0 | Workflow identity, team placement, credentials, runnable config overrides where appropriate, execution history, and workspace bindings are preserved |
| Studio user | Recognize high-risk updates | I can treat major version updates more carefully | P1 | Major SemVer updates show stronger warning copy and migration notes prominently in the confirmation dialog |

### Context, Problems, Opportunities
Candidate colleagues are fetched from the `AI-Colleagues/colleague-candidates` repository, parsed from `# /// orcheo` frontmatter, and onboarded through `POST /api/candidates/onboard`. Re-onboarding an existing candidate handle already appends a new workflow version, but this path is implicit and does not tell users which candidate version they currently have, whether a newer candidate exists, or what changed.

This creates an update lifecycle gap: candidate authors can improve workflows, but onboarded colleagues do not clearly show available updates or migration context. Users need an explicit package-like upgrade model: candidate releases have versions and notes; onboarded colleagues are pinned to a candidate version; updating is a deliberate action with preview and confirmation.

### Product goals and Non-goals
Goals:
- Add `version` to workflow frontmatter using strict SemVer `x.y.z`.
- Add versioned update notes to candidate metadata, including short summaries and optional migration guidance.
- Persist source candidate metadata on onboarded colleague workflow versions.
- Show update availability in Studio for onboarded candidate colleagues.
- Provide compact hover/focus context and a full confirmation dialog before update.
- Update selected colleagues by appending a new workflow version from the latest candidate release.

Non-goals:
- Automatic background updates of colleagues.
- Supporting arbitrary SemVer ranges or prerelease/build metadata in v1.
- Building a full candidate release registry outside the existing candidates repository.
- Bulk update flows in the first release.
- Diff visualization between candidate versions.
- Rewriting unrelated workflow version history or execution records.

## PRODUCT DEFINITION
### Requirements
- **P0: Frontmatter candidate version**
  - Add a top-level `version` field to `# /// orcheo` workflow frontmatter.
  - Accept only strict SemVer `MAJOR.MINOR.PATCH` with non-negative integer segments and no prerelease/build suffix in v1.
  - Candidate repository parsing must expose `version` in internal and public candidate API models.
  - Candidates without `version` remain visible but are treated as not update-trackable; they do not trigger update buttons.

- **P0: Versioned update notes**
  - Add an `updates` frontmatter table array where each entry has:
    - `version`: SemVer string matching a released candidate version.
    - `summary`: short user-facing summary.
    - `migration`: optional migration guide or caution.
  - `updates` entries are sorted by SemVer descending in API responses.
  - Existing `notes` remains supported as general candidate notes, not as release notes.

- **P0: Onboarded colleague source metadata**
  - Store source metadata when onboarding/updating from a candidate:
    - `source`: `candidate-onboard`
    - `candidate_id`
    - `candidate_handle`
    - `candidate_version`
    - `candidate_source_ref` where available from the fetched repository ref/cache snapshot
  - Existing onboarded colleagues without `candidate_version` are treated as unknown and do not show update availability until updated or re-onboarded with versioned metadata.

- **P0: Update availability detection**
  - Backend or Studio compares the latest candidate `version` with the onboarded colleague's latest source `candidate_version`.
  - Update available only when:
    - workflow latest version metadata indicates it came from the same `candidate_id` or matching candidate handle.
    - both versions are valid strict SemVer.
    - candidate version is greater than installed candidate version.
  - Missing or invalid versions do not show an update action.

- **P0: Studio update UX**
  - On onboarded colleague cards, show an update button when an update is available.
  - Hover and keyboard focus on the update button show compact context:
    - current installed version
    - latest candidate version
    - latest update summary
    - first migration warning when present
  - Clicking the update button opens a confirmation dialog with full version notes for the versions being crossed.
  - Confirmation copy distinguishes patch/minor updates from major updates.
  - Users can cancel without changing the colleague.

- **P0: Explicit update operation**
  - Add or refine an API endpoint that updates an existing onboarded colleague from a candidate by candidate id and workflow id.
  - The update appends a new workflow version using latest candidate script/config.
  - The update must preserve workflow identity, team assignment, handle, user-visible workflow shell fields unless explicitly replaced by candidate metadata policy, credentials, execution history, and workspace ownership.
  - On failure, the previous workflow version remains the active latest version.

- **P1: Documentation and author guidance**
  - Document candidate frontmatter version/update fields with examples.
  - Provide guidance for major, minor, and patch version bump expectations.
  - Document migration-note writing expectations for candidate authors.

### Designs (if applicable)
See `./2_design.md` for schema, API, request-flow, and UI details.

### Other Teams Impacted
- **Backend/API:** Candidate parser, public schema, onboard/update endpoint, source metadata persistence.
- **Studio:** Gallery card update action, hover/focus popover, confirmation dialog, update API integration.
- **Candidate authors:** Must add version and update notes to release-trackable candidates.
- **SDK/CLI:** Frontmatter parser validation and tests.

## TECHNICAL CONSIDERATIONS
### Architecture Overview
Candidate workflows continue to be sourced from the existing candidates repository and ingested server-side. The frontmatter parser gains version/update fields; the candidate service publishes those fields through `/api/candidates`; workflow version metadata records the installed candidate version; Studio compares installed and latest versions and calls an explicit update endpoint that appends a new workflow version.

### Technical Requirements
- Extend `WorkflowFrontmatter` and `parse_workflow_frontmatter` to support `version` and `updates`.
- Use a shared strict SemVer parser/comparator in backend and Studio, or compute update availability server-side to avoid duplicate comparison rules.
- Extend `CandidateItem` and `CandidatePublicItem` with `version` and `updates`.
- Include source candidate version metadata during onboarding and update.
- Preserve existing candidate cache stale-while-revalidate behavior; update availability may be stale until the candidate cache refreshes.
- Use idempotent storage migration only if a dedicated source metadata field is introduced; otherwise persist under existing workflow version metadata.
- Keep multi-workspace isolation intact using the hard-coded `X-Orcheo-Workspace` workspace context.

## MARKET DEFINITION
Internal platform and Studio enhancement; no external market analysis required.

## LAUNCH/ROLLOUT PLAN

### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| Primary: Versioned candidates parsed | 100% of candidates with valid SemVer expose version data through `/api/candidates` |
| Primary: Update decision quality | 100% of update buttons show current/latest versions and release-note context before mutation |
| Secondary: Update success rate | >=99% successful candidate colleague updates in staging and internal workspaces |
| Guardrail: No accidental overwrite | 0 update flows that remove workflow identity, team assignment, credentials, or history |
| Guardrail: Existing candidates unaffected | Candidates without version metadata remain onboardable with no update button |

### Rollout Strategy
Ship parser/schema support first, then backend update operation, then Studio update UI. Enable candidate authors to add versions progressively; unversioned candidates remain usable but do not participate in update detection.

### Experiment Plan (if applicable)
Not applicable. Validate with internal candidate workflows and automated regression coverage.

### Estimated Launch Phases (if applicable)
| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Backend/SDK tests | Add frontmatter parsing, candidate API fields, and source metadata persistence |
| **Phase 2** | Internal Studio | Add update availability, hover/focus summary, confirmation dialog, and update endpoint integration |
| **Phase 3** | Candidate authors | Document frontmatter versioning and update-note authoring; migrate official candidates gradually |

## HYPOTHESIS & RISKS
Hypothesis: explicit candidate versions and update notes will make candidate colleague upgrades safer and more understandable, increasing adoption of upstream improvements without surprising workspace users.

Risks:
- Users may confuse workflow version numbers with candidate semantic versions.
- Major updates may require manual migration that cannot be fully automated.
- Cached candidate data can temporarily hide newly released updates.
- Existing onboarded colleagues without source version metadata cannot reliably show update availability.

Risk mitigation:
- Label UI copy as "Candidate version" where ambiguity is likely.
- Show major-update warnings and full migration notes before confirmation.
- Keep stale-while-revalidate behavior but allow manual refresh as a follow-up if needed.
- Treat unknown installed versions conservatively and do not show update buttons without valid comparison data.

## APPENDIX
Example candidate frontmatter:

```python
# /// orcheo
# name = "Insight Analyst"
# handle = "insight-analyst"
# description = "Analyzes research inputs and produces concise insight summaries."
# version = "1.3.0"
# notes = "Best used with uploaded research documents."
#
# [[updates]]
# version = "1.3.0"
# summary = "Adds source-quality checks before insight synthesis."
# migration = "Review custom prompt overrides if they assume every source is accepted."
#
# [[updates]]
# version = "1.2.0"
# summary = "Improves citation formatting and final report structure."
# ///
```
