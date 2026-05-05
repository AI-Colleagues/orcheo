# Multi-Tenancy Demo Walkthrough

This walkthrough exercises the multi-tenant stack end to end:

- two tenants with distinct memberships
- concurrent workflow runs with quota enforcement
- tenant-scoped audit visibility
- soft-delete and hard-delete tooling for tenant cleanup

It should be read alongside the feature docs in
[`../project/initiatives/multi_tenancy/1_requirements.md`](../project/initiatives/multi_tenancy/1_requirements.md)
and [`../project/initiatives/multi_tenancy/2_design.md`](../project/initiatives/multi_tenancy/2_design.md).

## Prerequisites

1. Run the stack with tenancy enabled:
   ```bash
   export ORCHEO_MULTI_TENANCY_ENABLED=true
   export ORCHEO_DEFAULT_TENANT=default
   export REDIS_URL=redis://localhost:6379/0
   ```
2. Make sure the default tenant already exists. For a fresh install, the startup
   path in the rollout docs handles that automatically.
3. Have an operator token with admin rights for tenant management.

## Step 1: Create two tenants

Create separate tenants and assign distinct owners:

```bash
orcheo tenant create acme --name "Acme Inc" --owner alice
orcheo tenant create globex --name "Globex Ltd" --owner bob
```

Use `orcheo tenant list --all` to confirm both tenants exist.

## Step 2: Create a workflow in each tenant

Switch the active tenant before creating workflow resources:

```bash
orcheo tenant use acme
orcheo workflow create examples/quickstart/sdk_quickstart.py --name "Acme Demo"

orcheo tenant use globex
orcheo workflow create examples/quickstart/sdk_quickstart.py --name "Globex Demo"
```

Each workflow is stored with the active tenant id, so the two slugs stay isolated
even when the workflow definitions are identical.

## Step 3: Demonstrate concurrent-run quotas

Submit runs for the same tenant in quick succession:

```bash
orcheo tenant use acme
orcheo workflow run wf-acme --inputs '{"message": "first"}'
orcheo workflow run wf-acme --inputs '{"message": "second"}'
```

The first run should reserve the tenant slot, and the second run should fail
once the configured `max_concurrent_runs` limit is exceeded.

The quota enforcement path is implemented in the backend repository layer and
uses Redis when available, with an in-memory fallback for local development.

## Step 4: Verify tenant-scoped audit events

The admin audit-log command surfaces sensitive actions such as tenant creation,
membership changes, vault reads, token issuance, and tenant suspend/delete
events:

```bash
orcheo tenant audit-log <tenant-id>
```

You should see entries for:

- tenant creation
- membership changes
- tenant suspension or deletion
- token issuance or rotation

Those events are also stored in the `tenant_audit_events` table for later
inspection or export.

## Step 5: Soft-delete and purge

Soft-delete the tenant when you want to remove it from active use but keep the
row around for the retention window:

```bash
orcheo tenant deactivate <tenant-id>
orcheo tenant delete <tenant-id> --force
```

Then purge deleted tenants after the retention window expires:

```bash
orcheo tenant purge-deleted --retention-days 30
```

Use the purge command for GDPR-style cleanup flows once the deletion window has
elapsed.

## Recording Notes

If you are capturing a video walkthrough for internal docs or release notes,
record these moments:

1. tenant creation
2. tenant-scoped workflow creation
3. a concurrent run being rejected by quota
4. an audit-log lookup showing the sensitive actions
5. soft-delete followed by hard-delete/purge

That sequence shows the isolation contract, the governance layer, and the
operational cleanup path in one pass.
