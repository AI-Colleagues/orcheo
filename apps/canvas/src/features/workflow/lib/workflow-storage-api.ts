import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl, getBackendBaseUrl } from "@/lib/config";
import type {
  ApiWorkflow,
  ApiWorkflowCanvasPayload,
  ApiWorkflowRun,
  ApiWorkflowRunRemediation,
  ApiWorkflowVersion,
  CronTriggerConfig,
  PublicWorkflowMetadata,
  RequestOptions,
  SystemPluginsResponse,
  WorkflowListenerHealth,
  WorkflowListenerMetricsResponse,
  WorkflowCredentialReadinessResponse,
  WorkflowPublishResponse,
} from "./workflow-storage.types";

export const API_BASE = "/api/workflows";
export const PUBLIC_WORKFLOW_PATH = "/api/workflows";

const JSON_HEADERS = {
  Accept: "application/json",
  "Content-Type": "application/json",
};

export class ApiRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

const readText = async (response: Response): Promise<string> => {
  try {
    return await response.text();
  } catch {
    return "";
  }
};

export const request = async <T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> => {
  const expectJson = options.expectJson ?? true;
  const url = buildBackendHttpUrl(path);

  const response = await authFetch(url, {
    ...options,
    headers: options.body ? JSON_HEADERS : options.headers,
  });

  if (!response.ok) {
    const detail = (await readText(response)) || response.statusText;
    throw new ApiRequestError(detail, response.status);
  }

  if (!expectJson || response.status === 204) {
    return undefined as T;
  }

  const payload = await readText(response);
  if (!payload) {
    return undefined as T;
  }
  return JSON.parse(payload) as T;
};

export const fetchWorkflow = async (
  workflowId: string,
): Promise<ApiWorkflow | undefined> => {
  try {
    return await request<ApiWorkflow>(`${API_BASE}/${workflowId}`);
  } catch (error) {
    if (
      error instanceof ApiRequestError &&
      (error.status === 404 || error.status === 410)
    ) {
      return undefined;
    }
    throw error;
  }
};

export const fetchWorkflowCanvasData = async (
  workflowId: string,
): Promise<ApiWorkflowCanvasPayload | undefined> => {
  try {
    return await request<ApiWorkflowCanvasPayload>(
      `${API_BASE}/${workflowId}/canvas`,
    );
  } catch (error) {
    if (
      error instanceof ApiRequestError &&
      (error.status === 404 || error.status === 410)
    ) {
      return undefined;
    }
    throw error;
  }
};

export const fetchPublicWorkflow = async (
  workflowId: string,
): Promise<PublicWorkflowMetadata | undefined> => {
  try {
    return await request<PublicWorkflowMetadata>(
      `${PUBLIC_WORKFLOW_PATH}/${workflowId}/public`,
    );
  } catch (error) {
    if (
      error instanceof ApiRequestError &&
      (error.status === 404 || error.status === 410)
    ) {
      return undefined;
    }
    throw error;
  }
};

export const fetchWorkflowVersions = async (
  workflowId: string,
): Promise<ApiWorkflowVersion[]> => {
  try {
    return await request<ApiWorkflowVersion[]>(
      `${API_BASE}/${workflowId}/versions`,
    );
  } catch (error) {
    if (
      error instanceof ApiRequestError &&
      (error.status === 404 || error.status === 410)
    ) {
      return [];
    }
    throw error;
  }
};

export const fetchWorkflowCredentialReadiness = async (
  workflowId: string,
): Promise<WorkflowCredentialReadinessResponse | undefined> => {
  try {
    return await request<WorkflowCredentialReadinessResponse>(
      `${API_BASE}/${workflowId}/credentials/readiness`,
    );
  } catch (error) {
    if (
      error instanceof ApiRequestError &&
      (error.status === 404 || error.status === 410)
    ) {
      return undefined;
    }
    throw error;
  }
};

export const fetchSystemPlugins = async (): Promise<SystemPluginsResponse> =>
  request<SystemPluginsResponse>("/api/system/plugins");

export const fetchWorkflowListeners = async (
  workflowId: string,
): Promise<WorkflowListenerHealth[]> => {
  try {
    return await request<WorkflowListenerHealth[]>(
      `${API_BASE}/${workflowId}/listeners`,
    );
  } catch (error) {
    if (
      error instanceof ApiRequestError &&
      (error.status === 404 || error.status === 410)
    ) {
      return [];
    }
    throw error;
  }
};

export const fetchWorkflowListenerMetrics = async (
  workflowId: string,
): Promise<WorkflowListenerMetricsResponse | undefined> => {
  try {
    return await request<WorkflowListenerMetricsResponse>(
      `${API_BASE}/${workflowId}/listeners/metrics`,
    );
  } catch (error) {
    if (
      error instanceof ApiRequestError &&
      (error.status === 404 || error.status === 410)
    ) {
      return undefined;
    }
    throw error;
  }
};

const updateWorkflowListenerStatus = async (
  workflowId: string,
  subscriptionId: string,
  action: "pause" | "resume",
  actor = "canvas-app",
): Promise<WorkflowListenerHealth> => {
  return request<WorkflowListenerHealth>(
    `${API_BASE}/${workflowId}/listeners/${subscriptionId}/${action}`,
    {
      method: "POST",
      body: JSON.stringify({ actor }),
    },
  );
};

export const pauseWorkflowListener = async (
  workflowId: string,
  subscriptionId: string,
  actor?: string,
): Promise<WorkflowListenerHealth> =>
  updateWorkflowListenerStatus(workflowId, subscriptionId, "pause", actor);

export const resumeWorkflowListener = async (
  workflowId: string,
  subscriptionId: string,
  actor?: string,
): Promise<WorkflowListenerHealth> =>
  updateWorkflowListenerStatus(workflowId, subscriptionId, "resume", actor);

export const triggerWorkflowRun = async (
  workflowId: string,
  options: {
    triggeredBy?: string;
    inputs?: Record<string, unknown>;
    runnableConfig?: Record<string, unknown>;
  } = {},
): Promise<ApiWorkflowRun> => {
  const versions = await fetchWorkflowVersions(workflowId);
  const latestVersion = selectLatestWorkflowVersion(versions);
  if (!latestVersion) {
    throw new Error(
      "Canvas can only run workflows with an existing Python version. Ingest a Python script first.",
    );
  }

  return request<ApiWorkflowRun>(`${API_BASE}/${workflowId}/runs`, {
    method: "POST",
    body: JSON.stringify({
      workflow_version_id: latestVersion.id,
      triggered_by: options.triggeredBy ?? "canvas",
      input_payload: options.inputs ?? {},
      ...(options.runnableConfig
        ? { runnable_config: options.runnableConfig }
        : {}),
    }),
  });
};

export const fetchWorkflowRemediations = async (
  filters: {
    workflowId?: string;
    workflowVersionId?: string;
    runId?: string;
    status?: string;
    limit?: number;
  } = {},
): Promise<ApiWorkflowRunRemediation[]> => {
  const params = new URLSearchParams();
  if (filters.workflowId) {
    params.set("workflow_id", filters.workflowId);
  }
  if (filters.workflowVersionId) {
    params.set("workflow_version_id", filters.workflowVersionId);
  }
  if (filters.runId) {
    params.set("run_id", filters.runId);
  }
  if (filters.status) {
    params.set("status", filters.status);
  }
  if (filters.limit) {
    params.set("limit", String(filters.limit));
  }
  const query = params.toString();
  return request<ApiWorkflowRunRemediation[]>(
    `/api/workflow-remediations${query ? `?${query}` : ""}`,
  );
};

export const selectLatestWorkflowVersion = (
  versions: ApiWorkflowVersion[],
): ApiWorkflowVersion | undefined =>
  versions.reduce<ApiWorkflowVersion | undefined>(
    (latest, current) =>
      !latest || current.version > latest.version ? current : latest,
    undefined,
  );

const SCHEDULED_CONFIG_PATH = (workflowId: string) =>
  `${API_BASE}/${workflowId}/triggers/cron/config`;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

const toOptionalString = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim().length > 0 ? value : undefined;

const toOptionalNullableString = (
  value: unknown,
): string | null | undefined => {
  if (value === null) {
    return null;
  }
  return toOptionalString(value);
};

export const resolveWorkflowShareUrl = (
  workflow: Pick<ApiWorkflow, "id" | "handle" | "is_public" | "share_url">,
): string | null => {
  if (!workflow.is_public) {
    return null;
  }

  if (workflow.share_url) {
    return workflow.share_url;
  }

  const base = getBackendBaseUrl().replace(/\/+$/, "");
  const workflowRef = workflow.handle ?? workflow.id;
  return `${base}/chat/${workflowRef}`;
};

const extractCronConfigFromGraphIndex = (
  graph: Record<string, unknown>,
): CronTriggerConfig | null => {
  const index = graph.index;
  if (!isRecord(index) || !Array.isArray(index.cron)) {
    return null;
  }

  const entries = index.cron.filter(isRecord);
  if (entries.length === 0) {
    return null;
  }
  if (entries.length > 1) {
    throw new Error("Workflow contains multiple cron triggers.");
  }

  const entry = entries[0];
  const expression = toOptionalString(entry.expression);
  if (!expression) {
    return null;
  }

  return {
    expression,
    timezone: toOptionalString(entry.timezone),
    allow_overlapping:
      typeof entry.allow_overlapping === "boolean"
        ? entry.allow_overlapping
        : undefined,
    start_at: toOptionalNullableString(entry.start_at),
    end_at: toOptionalNullableString(entry.end_at),
  };
};

const extractCronConfigFromGraphNodes = (
  graph: Record<string, unknown>,
): CronTriggerConfig | null => {
  const graphFormat = toOptionalString(graph.format);
  const nodes =
    graphFormat === "langgraph-script" && isRecord(graph.summary)
      ? Array.isArray(graph.summary.nodes)
        ? graph.summary.nodes
        : []
      : Array.isArray(graph.nodes)
        ? graph.nodes
        : [];

  const cronNodes = nodes
    .filter(isRecord)
    .filter((node) => node.type === "CronTriggerNode");
  if (cronNodes.length === 0) {
    return null;
  }
  if (cronNodes.length > 1) {
    throw new Error("Workflow contains multiple cron triggers.");
  }

  const node = cronNodes[0];
  const expression = toOptionalString(node.expression);
  if (!expression) {
    return null;
  }

  return {
    expression,
    timezone: toOptionalString(node.timezone),
    allow_overlapping:
      typeof node.allow_overlapping === "boolean"
        ? node.allow_overlapping
        : undefined,
    start_at: toOptionalNullableString(node.start_at),
    end_at: toOptionalNullableString(node.end_at),
  };
};

export const extractCronConfigFromVersionGraph = (
  graph: unknown,
): CronTriggerConfig | null => {
  if (!isRecord(graph)) {
    throw new Error("Latest workflow version is missing graph data.");
  }

  const indexConfig = extractCronConfigFromGraphIndex(graph);
  if (indexConfig) {
    return indexConfig;
  }
  return extractCronConfigFromGraphNodes(graph);
};

const normalizeTemplateCronConfig = (
  config: CronTriggerConfig,
  metadata: unknown,
): CronTriggerConfig => {
  if (!isRecord(metadata)) {
    return config;
  }

  const templateId = toOptionalString(metadata.template_id);
  if (templateId !== "template-telegram-heartbeat") {
    return config;
  }

  if (config.expression === "* * * * *" && config.allow_overlapping !== true) {
    return {
      ...config,
      allow_overlapping: true,
    };
  }

  return config;
};

export const publishWorkflow = async (
  workflowId: string,
  options: {
    requireLogin?: boolean;
    actor?: string;
  } = {},
): Promise<{
  workflow: ApiWorkflow;
  shareUrl: string | null;
  message: string | null;
}> => {
  const payload = await request<WorkflowPublishResponse>(
    `${API_BASE}/${workflowId}/publish`,
    {
      method: "POST",
      body: JSON.stringify({
        require_login: options.requireLogin ?? false,
        actor: options.actor ?? "canvas",
      }),
    },
  );

  const workflow = payload.workflow;
  const shareUrl = payload.share_url ?? resolveWorkflowShareUrl(workflow);

  return {
    workflow: { ...workflow, share_url: shareUrl },
    shareUrl,
    message: payload.message ?? null,
  };
};

export const unpublishWorkflow = async (
  workflowId: string,
  actor = "canvas",
): Promise<{ workflow: ApiWorkflow; shareUrl: string | null }> => {
  const workflow = await request<ApiWorkflow>(
    `${API_BASE}/${workflowId}/publish/revoke`,
    {
      method: "POST",
      body: JSON.stringify({ actor }),
    },
  );

  return {
    workflow: { ...workflow, share_url: null },
    shareUrl: null,
  };
};

export const fetchCronTriggerConfig = async (
  workflowId: string,
): Promise<CronTriggerConfig | null> => {
  try {
    return await request<CronTriggerConfig>(SCHEDULED_CONFIG_PATH(workflowId));
  } catch (error) {
    if (
      error instanceof ApiRequestError &&
      (error.status === 404 || error.status === 410)
    ) {
      return null;
    }
    throw error;
  }
};

export const scheduleWorkflowFromLatestVersion = async (
  workflowId: string,
): Promise<{
  status: "scheduled" | "noop";
  config?: CronTriggerConfig;
  message: string;
}> => {
  const versions = await fetchWorkflowVersions(workflowId);
  const latest = versions
    .slice()
    .sort((left, right) => right.version - left.version)
    .at(0);

  if (!latest || !isRecord(latest.graph)) {
    throw new Error("Latest workflow version is missing graph data.");
  }

  const cronConfigRaw = extractCronConfigFromVersionGraph(latest.graph);
  const cronConfig = cronConfigRaw
    ? normalizeTemplateCronConfig(cronConfigRaw, latest.metadata)
    : null;
  if (!cronConfig) {
    return {
      status: "noop",
      message: `Workflow '${workflowId}' has no cron trigger to schedule.`,
    };
  }

  await request<CronTriggerConfig>(SCHEDULED_CONFIG_PATH(workflowId), {
    method: "PUT",
    body: JSON.stringify(cronConfig),
  });

  return {
    status: "scheduled",
    message: `Cron trigger scheduled for workflow '${workflowId}'.`,
    config: cronConfig,
  };
};

export const unscheduleWorkflow = async (
  workflowId: string,
): Promise<{ status: "unscheduled"; message: string }> => {
  await request<void>(SCHEDULED_CONFIG_PATH(workflowId), {
    method: "DELETE",
    expectJson: false,
  });

  return {
    status: "unscheduled",
    message: `Cron trigger unscheduled for workflow '${workflowId}'.`,
  };
};

export const upsertWorkflow = async (
  input: Pick<ApiWorkflow, "id" | "name" | "description" | "tags">,
  actor: string,
): Promise<string> => {
  if (!input.id) {
    const created = await request<ApiWorkflow>(API_BASE, {
      method: "POST",
      body: JSON.stringify({
        name: input.name,
        description: input.description,
        tags: input.tags ?? [],
        actor,
      }),
    });
    return created.id;
  }

  await request<ApiWorkflow>(`${API_BASE}/${input.id}`, {
    method: "PUT",
    body: JSON.stringify({
      name: input.name,
      description: input.description,
      tags: input.tags ?? [],
      actor,
    }),
  });
  return input.id;
};
