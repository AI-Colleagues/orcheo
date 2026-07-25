# Hosted Apps Endpoint, Role, and Audit Matrix

- **Author:** Codex
- **Owner:** ShaojieJiang
- **Date:** 2026-07-24

All workspace roles are resolved from the authenticated request and
`X-Orcheo-Workspace`; no actor or workspace identifier in a request body is
authoritative. Gateway routes use only the dedicated app-gateway identity and are
mounted outside the selected-workspace API lane.

| Mutation | Minimum authority | Audit action | Atomic side effects |
|---|---|---|---|
| Create app and initial alias | Editor | `app.create` | App, alias, audit |
| Update draft metadata | Editor | `app.update` | App, audit |
| Change visibility | Admin | `app.update` | Revision, app, audit |
| Archive app | Admin | `app.archive` | App, audit |
| Restore app | Admin | `app.restore` | App, audit |
| Reserve or replace alias | Admin | `alias.reserve` | New alias, prior tombstone, audit |
| Complete upload | Editor | `deployment.upload.complete` | Upload, deployment, validation outbox, audit |
| Publish or roll back | Admin | `release.publish` | Immutable release, active pointer, audit, invalidation outbox |
| Unpublish | Admin | `release.unpublish` | Publication state, audit, invalidation outbox |
| Draft binding/collection mutation | Admin | `capability.*` | Draft revision, audit |
| Suspend/reinstate app in workspace | Admin | `app.suspend` / `app.reinstate` | App overlay, audit, invalidation outbox |
| Platform block/reinstate | `hosted_apps:moderate` operator scope | `moderation.*` | Block, platform audit, invalidation outbox |
| Change global runtime generation | `hosted_apps:operate` operator scope | `runtime_generation.update` | Generation, platform audit, invalidation outbox |

The repository contract propagates audit persistence failures. A mutation must not
commit and then swallow an audit error. The Postgres implementation must issue the
state change, audit insert, and applicable outbox insert in one transaction.
