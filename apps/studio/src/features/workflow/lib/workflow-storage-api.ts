import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl, getBackendBaseUrl } from "@/lib/config";
import type {
  ApiTeam,
  ApiWorkflow,
  ApiWorkflowPagePayload,
  ApiWorkflowRun,
  ApiWorkflowVersion,
  CronTriggerConfig,
  PublicWorkflowMetadata,
  RequestOptions,
  SystemPluginsResponse,
  WorkflowListenerHealth,
  WorkflowListenerMetricsResponse,
  WorkflowCredentialReadinessResponse,
  WorkflowPublishResponse,
  WorkflowRunnableConfig,
  CandidateUpdateNote,
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

export const fetchWorkflowPageData = async (
  workflowId: string,
): Promise<ApiWorkflowPagePayload | undefined> => {
  try {
    return await request<ApiWorkflowPagePayload>(
      `${API_BASE}/${workflowId}/workflow`,
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
  context?: { workspaceSlug?: string; teamSlug?: string },
): Promise<PublicWorkflowMetadata | undefined> => {
  try {
    const params = new URLSearchParams();
    if (context?.workspaceSlug) {
      params.set("workspace_slug", context.workspaceSlug);
    }
    if (context?.teamSlug) {
      params.set("team_slug", context.teamSlug);
    }
    const queryString = params.toString();
    const url = `${PUBLIC_WORKFLOW_PATH}/${workflowId}/public${queryString ? `?${queryString}` : ""}`;
    return await request<PublicWorkflowMetadata>(url);
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

export interface ApiCandidate {
  id: string;
  handle: string;
  name: string;
  description: string | null;
  avatar: string | null;
  subtitle: string | null;
  notes: string | null;
  metadata: Record<string, unknown> | null;
  mermaid: string | null;
  version: string | null;
  updates: CandidateUpdateNote[];
}

export const fetchCandidates = async (): Promise<ApiCandidate[]> =>
  request<ApiCandidate[]>("/api/candidates");

export const fetchTeams = async (): Promise<ApiTeam[]> =>
  request<ApiTeam[]>("/api/teams");

export const createTeam = async (input: {
  name: string;
  slug: string;
}): Promise<ApiTeam> =>
  request<ApiTeam>("/api/teams", {
    method: "POST",
    body: JSON.stringify(input),
  });

export const deleteTeam = async (teamId: string): Promise<void> =>
  request<void>(`/api/teams/${teamId}`, {
    method: "DELETE",
    expectJson: false,
  });

export const onboardCandidate = async (
  candidateId: string,
  teamId?: string,
): Promise<ApiWorkflow> =>
  request<ApiWorkflow>("/api/candidates/onboard", {
    method: "POST",
    body: JSON.stringify({
      id: candidateId,
      ...(teamId ? { team_id: teamId } : {}),
    }),
  });

export const updateCandidateWorkflow = async (
  workflowId: string,
  candidateId: string,
): Promise<ApiWorkflow> =>
  request<ApiWorkflow>("/api/candidates/update", {
    method: "POST",
    body: JSON.stringify({
      workflow_id: workflowId,
      candidate_id: candidateId,
    }),
  });

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
  actor = "studio-app",
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
      "Studio can only run workflows with an existing Python version. Ingest a Python script first.",
    );
  }

  return request<ApiWorkflowRun>(`${API_BASE}/${workflowId}/runs`, {
    method: "POST",
    body: JSON.stringify({
      workflow_version_id: latestVersion.id,
      triggered_by: options.triggeredBy ?? "studio",
      input_payload: options.inputs ?? {},
      ...(options.runnableConfig
        ? { runnable_config: options.runnableConfig }
        : {}),
    }),
  });
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
  return `${base}/chat/${workflow.id}`;
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

// Matches a value that is exactly a single `{{config.configurable.<name>}}`
// placeholder, optionally padded with whitespace inside the braces.
const CONFIGURABLE_TEMPLATE = /^\{\{\s*config\.configurable\.([^}\s.]+)\s*\}\}$/;

const resolveConfigurableTemplate = (
  value: string | null | undefined,
  configurable: Record<string, unknown> | undefined,
): string | null | undefined => {
  if (typeof value !== "string") {
    return value;
  }
  const match = CONFIGURABLE_TEMPLATE.exec(value.trim());
  if (!match) {
    return value;
  }
  const name = match[1];
  const resolved = configurable?.[name];
  if (typeof resolved !== "string") {
    throw new Error(
      `Cron trigger references '{{config.configurable.${name}}}' but the ` +
        `workflow has no configurable value named '${name}'. Set a default ` +
        `for it in the workflow config.`,
    );
  }
  return resolved;
};

// Cron triggers may parametrize fields (e.g. `expression`) with
// `{{config.configurable.X}}` placeholders that the workflow runtime resolves
// from its `configurable` config. Scheduling needs the concrete value, so
// resolve any such placeholder against the version's configurable values
// before the payload is validated as a cron expression by the backend.
const resolveConfigurableCronConfig = (
  config: CronTriggerConfig,
  runnableConfig: WorkflowRunnableConfig | null | undefined,
): CronTriggerConfig => {
  const configurable = runnableConfig?.configurable;
  return {
    ...config,
    expression:
      resolveConfigurableTemplate(config.expression, configurable) ??
      config.expression,
    timezone: resolveConfigurableTemplate(config.timezone, configurable) as
      | string
      | undefined,
    start_at: resolveConfigurableTemplate(config.start_at, configurable) as
      | string
      | null
      | undefined,
    end_at: resolveConfigurableTemplate(config.end_at, configurable) as
      | string
      | null
      | undefined,
  };
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
        actor: options.actor ?? "studio",
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
  actor = "studio",
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
    ? normalizeTemplateCronConfig(
        resolveConfigurableCronConfig(cronConfigRaw, latest.runnable_config),
        latest.metadata,
      )
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
