# Hosted Apps author guide

Hosted Apps publishes an already-built static web application at one Orcheo-managed
wildcard alias. It never runs package installation, build scripts, SSR, containers, or
publisher server code.

## Bundle format

Upload a ZIP containing `index.html` at its root. Assets use normalized relative POSIX
paths. The validator enforces compressed and expanded size, file-count, per-file, and
path-depth limits. It rejects traversal, absolute paths, links and special files, native
executables, nested archives, duplicate/case-fold/Unicode-colliding names, and anything
under `__orcheo/`.

HTML must be UTF-8. Inline event handlers, `javascript:` URLs, and unsupported inline
script types are rejected. Supported inline scripts are SHA-256 hashed by the server and
included in the immutable release CSP. Service workers and installable/offline PWA
behavior are unsupported: every response applies non-relaxable `worker-src 'none'`.

### Declarative workflow bindings

An optional root `orcheo.app.json` file can request one or more named workflow
bindings:

```json
{
  "schema_version": 1,
  "bindings": {
    "summarize": {
      "workflow": "document-summarizer",
      "version": 3,
      "access_mode": "authenticated",
      "input_schema": {
        "type": "object",
        "required": ["text"],
        "properties": {
          "text": {"type": "string", "maxLength": 10000}
        },
        "additionalProperties": false
      },
      "output_projection": {"fields": ["final_state"]},
      "visitor_can_read_output": true,
      "limits": {"timeout_seconds": 120}
    }
  }
}
```

Binding keys are the logical names used by browser code. `workflow` is a portable
workflow ref or handle, while `version` is an exact positive version number. Upload
validates the manifest and preflights every workflow in the app workspace. The file is
private deployment metadata and is not served as an app asset.

Publishing is the authority boundary: an administrator acknowledges the updated
permission revision, Orcheo resolves the declarations again, and the release freezes
the exact workflow id, workflow version id, executable digest, runnable configuration,
schemas, disclosure flags, and limits. When `orcheo.app.json` is present, its bindings
are the complete binding set for that deployment; without it, existing control-plane
draft bindings continue to apply.

## Releases and caching

A deployment is immutable after validation. Publishing creates a new immutable release
that pairs one deployment with the exact approved binding and collection capability
snapshot. Draft changes do not affect the active release. Rollback creates another
audited release; it does not mutate an old release.

Alias paths and HTML revalidate. Private assets and all runtime responses use
`private, no-store`. Only platform-verifiable content-addressed URLs may use long-lived
immutable caching.

## Authentication

In P0, an authenticated visitor must be a current member of the publisher workspace.
External-customer identity is not supported. Public apps may use explicit anonymous
capabilities or offer optional login to workspace members.

Login uses a central authorization-code flow with PKCE. App JavaScript never receives
Studio access/refresh tokens, authorization codes, PKCE verifiers, or the app-session
secret. The app session is stored only in an exact-host `__Host-` HttpOnly Secure cookie.

## Runtime API

Reserved same-origin routes are:

- `POST /__orcheo/workflows/{binding}/runs`
- `GET /__orcheo/runs/{handle}`
- `/__orcheo/data/{collection}`
- `/__orcheo/auth/*`
- `GET /__orcheo/config`

Workflow calls use a logical binding name, never a workflow or workspace id. Every run
request requires an `Idempotency-Key`; retrying the same request returns the same opaque
app-run handle. Output is empty by default and is limited to the binding's explicit
projection, disclosure flags, and encoded byte limit. Errors are stable and sanitized.

Data collections are declared as `shared` or `user`. User collections require a current
workspace-member session. Collection deletion tombstones its stable id; recreating the
same display name cannot resurrect old records. Updates use optimistic versions and
lists use opaque cursors.

## Local HTTPS

Use `https://<alias>.apps.localhost` through Caddy's trusted local CA or another trusted
development wildcard certificate. Auth-disabled UI mode is useful for layout work but
does not count as login, cookie, redirect, Origin, or CSRF acceptance testing.

Common validation failures include `index_missing`, `unsafe_path`, `path_collision`,
`nested_archive`, `executable`, `reserved_path`, `inline_event_handler`, and
`javascript_url`. Fix the named bundle construct and upload a new immutable deployment.

## Examples

- [`examples/hosted_apps/hello-app`](../examples/hosted_apps/hello-app/README.md)
  is a minimal static-only bundle.
- [`examples/hosted_apps/workflow-app`](../examples/hosted_apps/workflow-app/README.md)
  uploads two restricted-mode-compatible workflows, declares the `greet` and
  `farewell` bindings in `orcheo.app.json`, invokes either from browser JavaScript,
  and polls the opaque app-run handle.
