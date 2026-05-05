# Multi-Workspace Audit

This note records the final verification pass for the multi-workspace rollout.
It covers the coverage check, the workspace-boundary security review, and the main
test slices used to validate the implementation.

It should be read together with:

- [`../project/initiatives/multi_workspace/1_requirements.md`](../project/initiatives/multi_workspace/1_requirements.md)
- [`../project/initiatives/multi_workspace/2_design.md`](../project/initiatives/multi_workspace/2_design.md)
- [`../project/initiatives/multi_workspace/3_plan.md`](../project/initiatives/multi_workspace/3_plan.md)
- [`multi_workspace_demo.md`](multi_workspace_demo.md)

## Verification Commands

Run these commands from the repository root:

```bash
uv run pytest --cov --cov-context=test -n auto
uv run coverage report --fail-under 95
uv run diff-cover coverage.xml --fail-under 100
```

For targeted workspace validation, the focused slice is:

```bash
uv run pytest \
  tests/workspace \
  tests/backend/test_workspace_governance.py \
  tests/backend/test_workspace_backend.py \
  tests/sdk/test_cli_workspace.py \
  -q
```

## Security Review Checklist

- Workspace resolution is centralized in the FastAPI workspace dependency and the
  request header only pins the active workspace when the authenticated principal
  already belongs to it.
- Repository calls require `workspace_id` for workspace-owned data and the lint-style
  checks reject query helpers that forget to reference it.
- Celery task headers carry `workspace_id`, and the worker rejects unscoped runs.
- WebSocket and webhook paths resolve workspace ownership before enqueuing or
  streaming any run state.
- Audit events are emitted for workspace creation, membership changes, vault reads,
  service-token lifecycle actions, and workspace suspend/delete/purge flows.
- Soft-delete keeps the workspace row around for the retention window; hard-delete
  tooling is available through the admin API and CLI once the window has
  expired.

## Results

| Check | Status | Notes |
| --- | --- | --- |
| Project coverage | Complete | Saved coverage data reports `98%` total coverage (`37,043` statements, `746` missed). |
| Diff coverage | Complete | `diff-cover` reports `100%` on the current diff against `origin/main`. |
| Security review | Complete | Cross-workspace reads/writes were traced through the auth, repository, Celery, WebSocket, and webhook layers. |

The coverage artifact and diff report were generated from the saved verification run after the final multi-workspace fixes landed.
