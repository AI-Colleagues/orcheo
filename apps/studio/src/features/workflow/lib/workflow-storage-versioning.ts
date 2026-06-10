import { toStoredWorkflow } from "./workflow-storage-helpers";
import {
  API_BASE,
  fetchWorkflowCanvasData,
  fetchWorkflowVersions,
  request,
} from "./workflow-storage-api";
import type {
  ApiWorkflowCanvasData,
  ApiWorkflowCanvasPayload,
  ApiWorkflowVersion,
  StoredWorkflow,
  WorkflowRunnableConfig,
} from "./workflow-storage.types";

interface WorkflowCacheEntry {
  workflow: StoredWorkflow;
  cachedAt: number;
}

const WORKFLOW_CACHE_TTL_MS = 10_000;
const workflowCache = new Map<string, WorkflowCacheEntry>();
const workflowInflight = new Map<string, Promise<StoredWorkflow | undefined>>();

const isApiWorkflowCanvasData = (
  payload: ApiWorkflowCanvasPayload,
): payload is ApiWorkflowCanvasData =>
  typeof payload === "object" &&
  payload !== null &&
  "workflow" in payload &&
  typeof (payload as ApiWorkflowCanvasData).workflow?.id === "string";

export const primeWorkflowCache = (workflow: StoredWorkflow): void => {
  workflowCache.set(workflow.id, {
    workflow,
    cachedAt: Date.now(),
  });
};

export const invalidateWorkflowCache = (workflowId?: string): void => {
  if (workflowId) {
    workflowCache.delete(workflowId);
    workflowInflight.delete(workflowId);
    return;
  }
  workflowCache.clear();
  workflowInflight.clear();
};

export const ensureWorkflow = async (
  workflowId: string,
): Promise<StoredWorkflow | undefined> => {
  const cached = workflowCache.get(workflowId);
  if (cached && Date.now() - cached.cachedAt < WORKFLOW_CACHE_TTL_MS) {
    return cached.workflow;
  }

  const inflight = workflowInflight.get(workflowId);
  if (inflight) {
    return inflight;
  }

  const request = (async () => {
    const payload = await fetchWorkflowCanvasData(workflowId);
    if (!payload) {
      workflowCache.delete(workflowId);
      return undefined;
    }
    const canvasHasWorkflow = isApiWorkflowCanvasData(payload);
    const workflowPayload = canvasHasWorkflow ? payload.workflow : payload;
    let versions = canvasHasWorkflow ? payload.versions : undefined;
    if (versions === undefined) {
      versions = await fetchWorkflowVersions(workflowId);
    }
    const workflow = toStoredWorkflow(workflowPayload, versions);
    primeWorkflowCache(workflow);
    return workflow;
  })();
  workflowInflight.set(workflowId, request);

  try {
    return await request;
  } finally {
    if (workflowInflight.get(workflowId) === request) {
      workflowInflight.delete(workflowId);
    }
  }
};

const resolveTargetVersion = (
  versions: ApiWorkflowVersion[],
  versionNumber?: number,
): number => {
  if (versions.length === 0) {
    throw new Error(
      "Canvas can only save config for workflows with an existing Python version. Ingest a Python script first.",
    );
  }
  if (versionNumber !== undefined) {
    const matched = versions.find((entry) => entry.version === versionNumber);
    if (!matched) {
      throw new Error(`Workflow version ${versionNumber} not found.`);
    }
    return versionNumber;
  }
  return Math.max(...versions.map((entry) => entry.version));
};

export const persistRunnableConfig = async (
  workflowId: string,
  actor: string,
  runnableConfig: WorkflowRunnableConfig | null,
  versionNumber?: number,
) => {
  const versions = await fetchWorkflowVersions(workflowId);
  const targetVersion = resolveTargetVersion(versions, versionNumber);
  await request(
    `${API_BASE}/${workflowId}/versions/${targetVersion}/runnable-config`,
    {
      method: "PUT",
      body: JSON.stringify({
        runnable_config: runnableConfig,
        actor,
      }),
    },
  );
};
