import {
  assertWorkflowTemplateCompatibility,
  getWorkflowTemplateDefinition,
} from "@features/workflow/data/workflow-data";
import {
  DEFAULT_ACTOR,
  WORKFLOW_STORAGE_EVENT,
} from "./workflow-storage.constants";
import {
  getWorkflowRouteRef,
  toStoredWorkflow,
} from "./workflow-storage-helpers";
import {
  API_BASE,
  fetchSystemPlugins,
  fetchWorkflowVersions,
  request,
  upsertWorkflow,
} from "./workflow-storage-api";
import {
  ensureWorkflow,
  invalidateWorkflowCache,
  persistRunnableConfig,
  primeWorkflowCache,
} from "./workflow-storage-versioning";
import type {
  ApiWorkflow,
  SaveWorkflowInput,
  SaveWorkflowOptions,
  StoredWorkflow,
} from "./workflow-storage.types";

interface ListWorkflowsOptions {
  forceRefresh?: boolean;
}

interface TemplateWorkflowOverrides {
  name?: string;
  description?: string;
  tags?: string[];
  handle?: string | null;
}

interface WorkflowListCacheEntry {
  items: StoredWorkflow[];
  cachedAt: number;
}

const WORKFLOW_LIST_CACHE_TTL_MS = 5 * 60 * 1000;
let workflowListCache: WorkflowListCacheEntry | undefined;
let workflowListInflight: Promise<StoredWorkflow[]> | undefined;
let workflowListRequestId = 0;

const resolveActor = (actor?: string): string => {
  const explicitActor = actor?.trim();
  if (explicitActor) {
    return explicitActor;
  }

  return DEFAULT_ACTOR;
};

const emitUpdate = () => {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new CustomEvent(WORKFLOW_STORAGE_EVENT));
};

const escapeRegExp = (value: string): string =>
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const resolveTemplateWorkflowName = async (
  baseName: string,
): Promise<string> => {
  const normalizedBaseName = baseName.trim();
  if (!normalizedBaseName) {
    return baseName;
  }

  const namePattern = new RegExp(
    `^${escapeRegExp(normalizedBaseName)}\\s(\\d+)$`,
  );
  const workflows = await request<ApiWorkflow[]>(API_BASE);
  const usedSuffixes = new Set<number>();

  for (const workflow of workflows) {
    if (workflow.is_archived) {
      continue;
    }

    const workflowName = workflow.name.trim();
    if (workflowName === normalizedBaseName) {
      usedSuffixes.add(0);
      continue;
    }

    const match = workflowName.match(namePattern);
    if (!match) {
      continue;
    }

    const suffix = Number.parseInt(match[1] ?? "", 10);
    if (Number.isInteger(suffix) && suffix >= 1) {
      usedSuffixes.add(suffix);
    }
  }

  if (!usedSuffixes.has(0)) {
    return normalizedBaseName;
  }

  let suffix = 1;
  while (usedSuffixes.has(suffix)) {
    suffix += 1;
  }

  return `${normalizedBaseName} ${suffix}`;
};

const buildTemplateCanvasMetadata = ({
  name,
  description,
  nodes,
  edges,
}: Pick<SaveWorkflowInput, "name" | "description" | "nodes" | "edges">) => ({
  snapshot: {
    name,
    description,
    nodes,
    edges,
  },
  summary: { added: 0, removed: 0, modified: 0 },
});

const assertTemplatePluginRequirements = async (
  requiredPlugins: string[] | undefined,
): Promise<void> => {
  if (!requiredPlugins || requiredPlugins.length === 0) {
    return;
  }
  const payload = await fetchSystemPlugins();
  const available = new Set(
    payload.plugins
      .filter((plugin) => plugin.enabled && plugin.loaded)
      .map((plugin) => plugin.name),
  );
  const missing = requiredPlugins.filter((plugin) => !available.has(plugin));
  if (missing.length === 0) {
    return;
  }
  throw new Error(
    `Install required plugins before using this template: ${missing.join(", ")}`,
  );
};

export const invalidateWorkflowListCache = () => {
  workflowListCache = undefined;
  workflowListInflight = undefined;
  invalidateWorkflowCache();
};

export const listWorkflows = async (
  options: ListWorkflowsOptions = {},
): Promise<StoredWorkflow[]> => {
  const forceRefresh = options.forceRefresh ?? false;
  const now = Date.now();
  const cacheAge = workflowListCache ? now - workflowListCache.cachedAt : null;
  const cacheIsFresh =
    cacheAge !== null && cacheAge < WORKFLOW_LIST_CACHE_TTL_MS;

  if (!forceRefresh && cacheIsFresh && workflowListCache) {
    return workflowListCache.items;
  }

  if (!forceRefresh && workflowListInflight) {
    return workflowListInflight;
  }

  workflowListRequestId += 1;
  const requestId = workflowListRequestId;

  const inflightPromise = (async () => {
    const workflows = await request<ApiWorkflow[]>(API_BASE);
    const activeWorkflows = workflows.filter(
      (workflow) => workflow.is_archived !== true,
    );
    const items = await Promise.all(
      activeWorkflows.map(async (workflow) => {
        if (workflow.latest_version !== undefined) {
          const versions = workflow.latest_version
            ? [workflow.latest_version]
            : [];
          return toStoredWorkflow(workflow, versions);
        }
        const versions = await fetchWorkflowVersions(workflow.id);
        return toStoredWorkflow(workflow, versions);
      }),
    );
    if (requestId === workflowListRequestId) {
      workflowListCache = { items, cachedAt: Date.now() };
    }
    return items;
  })();

  workflowListInflight = inflightPromise;

  try {
    return await inflightPromise;
  } finally {
    if (workflowListInflight === inflightPromise) {
      workflowListInflight = undefined;
    }
  }
};

export const getWorkflowById = async (
  workflowId: string,
): Promise<StoredWorkflow | undefined> => {
  return ensureWorkflow(workflowId);
};

export const saveWorkflow = async (
  input: SaveWorkflowInput,
  options?: SaveWorkflowOptions,
): Promise<StoredWorkflow> => {
  const actor = resolveActor(options?.actor);
  const workflowId = await upsertWorkflow(input, actor);
  if (options?.runnableConfig !== undefined) {
    await persistRunnableConfig(workflowId, actor, options.runnableConfig);
  }

  const stored = await ensureWorkflow(workflowId);
  if (!stored) {
    throw new Error("Failed to load persisted workflow");
  }

  invalidateWorkflowListCache();
  primeWorkflowCache(stored);
  emitUpdate();
  return stored;
};

export const saveWorkflowMetadata = async (
  input: Pick<SaveWorkflowInput, "id" | "name" | "description" | "tags">,
  options?: Pick<SaveWorkflowOptions, "actor">,
): Promise<StoredWorkflow> => {
  if (!input.id) {
    throw new Error("Workflow id is required to save workflow metadata.");
  }

  const actor = resolveActor(options?.actor);
  await upsertWorkflow(
    {
      id: input.id,
      name: input.name,
      description: input.description,
      tags: input.tags,
    },
    actor,
  );

  const stored = await ensureWorkflow(input.id);
  if (!stored) {
    throw new Error("Failed to load persisted workflow metadata");
  }

  invalidateWorkflowListCache();
  primeWorkflowCache(stored);
  emitUpdate();
  return stored;
};

export const createWorkflowFromTemplate = async (
  templateId: string,
  overrides?: TemplateWorkflowOverrides,
): Promise<StoredWorkflow | undefined> => {
  const templateDefinition = getWorkflowTemplateDefinition(templateId);
  if (!templateDefinition) {
    return undefined;
  }
  assertWorkflowTemplateCompatibility(templateDefinition);
  await assertTemplatePluginRequirements(
    templateDefinition.metadata?.requiredPlugins,
  );

  const actor = resolveActor();
  const templateWorkflow = templateDefinition.workflow;
  const workflowName =
    overrides?.name ??
    (await resolveTemplateWorkflowName(templateWorkflow.name));
  const workflowDescription =
    overrides?.description ?? templateWorkflow.description;
  const workflowTags =
    overrides?.tags ??
    templateWorkflow.tags.filter((tag) => tag !== "template");

  const created = await request<ApiWorkflow>(API_BASE, {
    method: "POST",
    body: JSON.stringify({
      name: workflowName,
      handle: overrides?.handle ?? templateWorkflow.handle ?? undefined,
      description: workflowDescription,
      tags: workflowTags,
      actor,
    }),
  });

  await request(`${API_BASE}/${created.id}/versions/ingest`, {
    method: "POST",
    body: JSON.stringify({
      script: templateDefinition.script,
      entrypoint: templateDefinition.entrypoint ?? null,
      runnable_config: templateDefinition.runnableConfig ?? null,
      metadata: {
        source: "canvas-template",
        template_id: templateWorkflow.id,
        template: templateDefinition.metadata ?? null,
        canvas: buildTemplateCanvasMetadata({
          name: workflowName,
          description: workflowDescription,
          nodes: templateWorkflow.nodes,
          edges: templateWorkflow.edges,
        }),
      },
      notes: templateDefinition.notes,
      created_by: actor,
    }),
  });

  const stored = await ensureWorkflow(created.id);
  if (!stored) {
    throw new Error("Failed to load workflow created from template");
  }

  invalidateWorkflowListCache();
  primeWorkflowCache(stored);
  emitUpdate();
  return stored;
};

export const getVersionSnapshot = async (
  workflowId: string,
  versionId: string,
): Promise<WorkflowSnapshot | undefined> => {
  const workflow = await getWorkflowById(workflowId);
  return workflow?.versions.find((entry) => entry.id === versionId)?.snapshot;
};

export const deleteWorkflow = async (
  workflowId: string,
  actor?: string,
): Promise<void> => {
  const resolvedActor = resolveActor(actor);
  await request<void>(
    `${API_BASE}/${workflowId}?actor=${encodeURIComponent(resolvedActor)}`,
    { method: "DELETE", expectJson: false },
  );
  invalidateWorkflowCache(workflowId);
  invalidateWorkflowListCache();
  emitUpdate();
};

export type {
  StoredWorkflow,
  WorkflowVersionRecord,
  SaveWorkflowInput,
  SaveWorkflowOptions,
} from "./workflow-storage.types";

export { WORKFLOW_STORAGE_EVENT } from "./workflow-storage.constants";
export { getWorkflowRouteRef };
