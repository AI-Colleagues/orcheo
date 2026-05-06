# Multi-Workspace Demo Walkthrough

This walkthrough exercises the multi-workspace stack end to end:

- two workspaces with distinct memberships
- concurrent workflow runs with quota enforcement
- workspace-scoped audit visibility
- soft-delete and hard-delete tooling for workspace cleanup

It should be read alongside the feature docs in
[`../project/initiatives/multi_workspace/1_requirements.md`](../project/initiatives/multi_workspace/1_requirements.md)
and [`../project/initiatives/multi_workspace/2_design.md`](../project/initiatives/multi_workspace/2_design.md).

## Prerequisites

1. Run the stack with workspace scoping enabled:
   ```bash
   export ORCHEO_MULTI_WORKSPACE_ENABLED=true
   export ORCHEO_MULTI_WORKSPACE_DEFAULT_WORKSPACE_SLUG=default
   export REDIS_URL=redis://localhost:6379/0
   ```
2. Make sure the default workspace already exists. For a fresh install, the startup
   path in the rollout docs handles that automatically.
3. Have an operator token with admin rights for workspace management.

## Step 1: Create two workspaces

Create separate workspaces and assign distinct owners:

```bash
orcheo workspace create acme --name "Acme Inc" --owner alice
orcheo workspace create globex --name "Globex Ltd" --owner bob
```

Use `orcheo workspace list --all` to confirm both workspaces exist.

## Step 2: Create a workflow in each workspace

Switch the active workspace before creating workflow resources:

```bash
orcheo workspace use acme
orcheo workflow create examples/quickstart/sdk_quickstart.py --name "Acme Demo"

orcheo workspace use globex
orcheo workflow create examples/quickstart/sdk_quickstart.py --name "Globex Demo"
```

Each workflow is stored with the active workspace id, so the two slugs stay isolated
even when the workflow definitions are identical.

## Step 3: Demonstrate concurrent-run quotas

Submit runs for the same workspace in quick succession:

```bash
orcheo workspace use acme
orcheo workflow run wf-acme --inputs '{"message": "first"}'
orcheo workflow run wf-acme --inputs '{"message": "second"}'
```

The first run should reserve the workspace slot, and the second run should fail
once the configured `max_concurrent_runs` limit is exceeded.

The quota enforcement path is implemented in the backend repository layer and
uses Redis when available, with an in-memory fallback for local development.

## Step 4: Verify workspace-scoped audit events

The admin audit-log command surfaces sensitive actions such as workspace creation,
membership changes, vault reads, token issuance, and workspace suspend/delete
events:

```bash
orcheo workspace audit-log <workspace-id>
```

You should see entries for:

- workspace creation
- membership changes
- workspace suspension or deletion
- token issuance or rotation

Those events are also stored in the `workspace_audit_events` table for later
inspection or export.

## Step 5: Soft-delete and purge

Soft-delete the workspace when you want to remove it from active use but keep the
row around for the retention window:

```bash
orcheo workspace deactivate <workspace-id>
orcheo workspace delete <workspace-id> --force
```

Then purge deleted workspaces after the retention window expires:

```bash
orcheo workspace purge-deleted --retention-days 30
```

Use the purge command for GDPR-style cleanup flows once the deletion window has
elapsed.

## Recording Notes

If you are capturing a video walkthrough for internal docs or release notes,
record these moments:

1. workspace creation
2. workspace-scoped workflow creation
3. a concurrent run being rejected by quota
4. an audit-log lookup showing the sensitive actions
5. soft-delete followed by hard-delete/purge

That sequence shows the isolation contract, the governance layer, and the
operational cleanup path in one pass.
