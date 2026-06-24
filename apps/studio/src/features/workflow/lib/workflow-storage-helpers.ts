import type { Edge as FlowEdge, Node as FlowNode } from "@xyflow/react";
import type { RJSFSchema } from "@rjsf/utils";
import {
  getWorkflowTemplateDefinition,
  type Workflow,
  type WorkflowEdge,
  type WorkflowNode,
} from "@features/workflow/data/workflow-data";
import type { WorkflowDiffResult, WorkflowSnapshot } from "./workflow-diff";
import {
  DEFAULT_OWNER,
  DEFAULT_SUMMARY,
  HISTORY_LIMIT,
} from "./workflow-storage.constants";
import {
  forceMermaidLeftToRight,
  normalizeMermaidPalette,
} from "./mermaid-renderer";
import type {
  ApiWorkflow,
  ApiWorkflowVersion,
  ApiWorkflowVersionSummary,
  WorkflowVersionMetadata,
  StoredWorkflow,
  WorkflowVersionRecord,
} from "./workflow-storage.types";

export const ensureArray = <T>(value: T[] | undefined): T[] =>
  Array.isArray(value) ? value : [];

export const cloneNodes = (nodes: WorkflowNode[]): WorkflowNode[] =>
  nodes.map((node) => ({
    ...node,
    position: { ...node.position },
    data: { ...node.data },
  }));

export const cloneEdges = (edges: WorkflowEdge[]): WorkflowEdge[] =>
  edges.map((edge) => ({ ...edge }));

export const emptySnapshot = (
  name: string,
  description?: string,
): WorkflowSnapshot => ({
  name,
  description,
  nodes: [],
  edges: [],
});

const toVersionLabel = (version: number): string =>
  `v${version.toString().padStart(2, "0")}`;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

const graphHasCronTrigger = (graph: unknown): boolean => {
  if (!isRecord(graph)) {
    return false;
  }

  const index = graph.index;
  if (isRecord(index) && Array.isArray(index.cron) && index.cron.length > 0) {
    return true;
  }

  const nodes = graph.nodes;
  if (Array.isArray(nodes)) {
    return nodes.some(
      (node) => isRecord(node) && node.type === "CronTriggerNode",
    );
  }

  const summary = graph.summary;
  if (isRecord(summary) && Array.isArray(summary.nodes)) {
    return summary.nodes.some(
      (node) => isRecord(node) && node.type === "CronTriggerNode",
    );
  }

  return false;
};

const extractAvatarEmoji = (metadata: unknown): string | undefined => {
  if (!isRecord(metadata)) {
    return undefined;
  }

  const value = metadata.avatar;
  if (typeof value === "string") {
    const normalized = value.trim();
    if (normalized) {
      return normalized;
    }
  }

  return undefined;
};

const extractCandidateSource = (
  metadata: unknown,
): WorkflowVersionMetadata["candidateSource"] => {
  if (!isRecord(metadata) || metadata.source !== "candidate-onboard") {
    return undefined;
  }

  return {
    candidateId:
      typeof metadata.candidate_id === "string"
        ? metadata.candidate_id
        : undefined,
    candidateHandle:
      typeof metadata.candidate_handle === "string"
        ? metadata.candidate_handle
        : undefined,
    candidateVersion:
      typeof metadata.candidate_version === "string"
        ? metadata.candidate_version
        : undefined,
    candidateSourceRef:
      typeof metadata.candidate_source_ref === "string"
        ? metadata.candidate_source_ref
        : undefined,
  };
};

const toAuthor = (id: string | undefined): Workflow["owner"] => {
  if (!id) {
    return { ...DEFAULT_OWNER };
  }
  return {
    ...DEFAULT_OWNER,
    id: id || DEFAULT_OWNER.id,
    name: id || DEFAULT_OWNER.name,
  };
};

export const toFlowNodes = (nodes: WorkflowNode[]): FlowNode[] =>
  nodes.map(
    (node) =>
      ({
        id: node.id,
        type: node.type,
        position: node.position,
        data: node.data,
      }) satisfies FlowNode,
  );

export const toFlowEdges = (edges: WorkflowEdge[]): FlowEdge[] =>
  edges.map(
    (edge) =>
      ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.sourceHandle,
        targetHandle: edge.targetHandle,
        label: edge.label,
        type: edge.type,
      }) satisfies FlowEdge,
  );

export const getWorkflowRouteRef = (
  workflow:
    | Pick<ApiWorkflow, "id" | "handle" | "team_id">
    | Pick<Workflow, "id" | "handle" | "teamId">,
): string => {
  const teamId = "team_id" in workflow ? workflow.team_id : workflow.teamId;
  if (teamId) {
    return workflow.id;
  }
  return workflow.handle ?? workflow.id;
};

export const resolveWorkflowVersionMermaidSource = (
  version:
    | Pick<WorkflowVersionRecord, "mermaid" | "templateId">
    | Pick<NonNullable<Workflow["versions"]>[number], "mermaid" | "templateId">
    | null
    | undefined,
): string | null => {
  const source =
    version?.mermaid ??
    (version?.templateId != null
      ? getWorkflowTemplateDefinition(
          version.templateId,
        )?.workflow.versions?.at(-1)?.mermaid
      : undefined);
  if (!source) {
    return null;
  }

  const trimmedSource = source.trim();
  return trimmedSource.length > 0
    ? normalizeMermaidPalette(forceMermaidLeftToRight(trimmedSource))
    : null;
};

const parseWorkflowMetadata = (
  metadata: unknown,
  fallbackName: string,
  fallbackDescription?: string,
): WorkflowVersionMetadata => {
  const configurableSchemas = parseConfigurableSchemas(
    isRecord(metadata) ? metadata.configurable_schema : undefined,
  );
  const avatarEmoji = extractAvatarEmoji(metadata);
  const candidateSource = extractCandidateSource(metadata);
  const resolveTemplateFallback = (): WorkflowVersionMetadata | undefined => {
    if (!metadata || typeof metadata !== "object") {
      return undefined;
    }

    const templateId = (metadata as Record<string, unknown>).template_id;
    if (typeof templateId !== "string" || templateId.length === 0) {
      return undefined;
    }

    const templateDefinition = getWorkflowTemplateDefinition(templateId);
    if (!templateDefinition) {
      return undefined;
    }

    return {
      snapshot: {
        name: fallbackName,
        description: fallbackDescription,
        nodes: cloneNodes(templateDefinition.workflow.nodes),
        edges: cloneEdges(templateDefinition.workflow.edges),
      },
      summary: { ...DEFAULT_SUMMARY },
      templateId,
      configurableSchemas,
      avatarEmoji,
      candidateSource,
    };
  };

  if (!metadata || typeof metadata !== "object") {
    return {
      snapshot: emptySnapshot(fallbackName, fallbackDescription),
      summary: { ...DEFAULT_SUMMARY },
      templateId: undefined,
      configurableSchemas: undefined,
      avatarEmoji: undefined,
      candidateSource: undefined,
    };
  }

  const metadataRecord = metadata as Record<string, unknown>;
  const workflowMetadata = metadataRecord.workflow ?? metadataRecord.canvas;
  if (!workflowMetadata || typeof workflowMetadata !== "object") {
    return (
      resolveTemplateFallback() ?? {
        snapshot: emptySnapshot(fallbackName, fallbackDescription),
        summary: { ...DEFAULT_SUMMARY },
        configurableSchemas,
        avatarEmoji,
        candidateSource,
      }
    );
  }

  const workflowRecord = workflowMetadata as Record<string, unknown>;
  const snapshotPayload = workflowRecord.snapshot as
    | WorkflowSnapshot
    | undefined;
  const summaryPayload = workflowRecord.summary as
    | WorkflowDiffResult["summary"]
    | undefined;
  const messagePayload = workflowRecord.message as string | undefined;
  const workflowToGraph = (workflowRecord.workflowToGraph ??
    workflowRecord.canvasToGraph) as Record<string, string> | undefined;
  const graphToWorkflow = (workflowRecord.graphToWorkflow ??
    workflowRecord.graphToCanvas) as Record<string, string> | undefined;
  const templateId =
    typeof (metadata as Record<string, unknown>).template_id === "string"
      ? ((metadata as Record<string, unknown>).template_id as string)
      : undefined;

  const snapshot = snapshotPayload
    ? {
        name:
          typeof snapshotPayload.name === "string"
            ? snapshotPayload.name
            : fallbackName,
        description:
          typeof snapshotPayload.description === "string"
            ? snapshotPayload.description
            : fallbackDescription,
        nodes: ensureArray(snapshotPayload.nodes),
        edges: ensureArray(snapshotPayload.edges),
      }
    : (resolveTemplateFallback()?.snapshot ??
      emptySnapshot(fallbackName, fallbackDescription));

  const summary = summaryPayload
    ? {
        added: summaryPayload.added ?? 0,
        removed: summaryPayload.removed ?? 0,
        modified: summaryPayload.modified ?? 0,
      }
    : { ...DEFAULT_SUMMARY };

  return {
    snapshot,
    summary,
    message: messagePayload,
    workflowToGraph,
    graphToWorkflow,
    templateId,
    configurableSchemas,
    avatarEmoji,
    candidateSource,
  };
};

const parseConfigurableSchemas = (
  value: unknown,
): Record<string, RJSFSchema> | undefined => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }

  const entries = Object.entries(value).filter(
    ([, schema]) =>
      schema !== null && typeof schema === "object" && !Array.isArray(schema),
  );

  if (entries.length === 0) {
    return undefined;
  }

  return Object.fromEntries(entries) as Record<string, RJSFSchema>;
};

const toVersionRecord = (
  version: ApiWorkflowVersionSummary | ApiWorkflowVersion,
  workflowName: string,
  workflowDescription?: string,
): WorkflowVersionRecord => {
  const metadata = parseWorkflowMetadata(
    version.metadata,
    workflowName,
    workflowDescription ?? undefined,
  );

  const message =
    metadata.message ??
    version.notes ??
    `Updated from Studio on ${new Date(version.created_at).toLocaleString()}`;

  return {
    id: version.id,
    version: toVersionLabel(version.version),
    versionNumber: version.version,
    timestamp: version.created_at,
    message,
    author: toAuthor(version.created_by),
    summary: metadata.summary ?? { ...DEFAULT_SUMMARY },
    snapshot:
      metadata.snapshot ?? emptySnapshot(workflowName, workflowDescription),
    mermaid: version.mermaid ?? null,
    hasCronTrigger:
      version.has_cron_trigger ??
      graphHasCronTrigger((version as ApiWorkflowVersion).graph),
    runnableConfig: version.runnable_config ?? null,
    configurableSchemas: metadata.configurableSchemas,
    graphToWorkflow: metadata.graphToWorkflow,
    templateId: metadata.templateId,
    avatarEmoji: metadata.avatarEmoji,
    candidateSource: metadata.candidateSource,
  };
};

export const toStoredWorkflow = (
  workflow: ApiWorkflow,
  versions?: ApiWorkflowVersionSummary[],
): StoredWorkflow => {
  const versionRecords = ensureArray(versions)
    .map((entry) =>
      toVersionRecord(entry, workflow.name, workflow.description ?? undefined),
    )
    .slice(-HISTORY_LIMIT);

  const latestSnapshot =
    versionRecords.at(-1)?.snapshot ??
    emptySnapshot(workflow.name, workflow.description ?? undefined);
  const avatarEmoji = versionRecords.at(-1)?.avatarEmoji ?? undefined;

  return {
    id: workflow.id,
    handle: workflow.handle ?? undefined,
    teamId: workflow.team_id ?? undefined,
    name: workflow.name,
    description: workflow.description ?? undefined,
    avatarEmoji,
    draftAccess: workflow.draft_access,
    createdAt: workflow.created_at,
    updatedAt: workflow.updated_at,
    owner: toAuthor(undefined),
    tags: ensureArray(workflow.tags),
    nodes: cloneNodes(latestSnapshot.nodes),
    edges: cloneEdges(latestSnapshot.edges),
    versions: versionRecords,
    sourceExample: undefined,
    lastRun: undefined,
    isArchived: workflow.is_archived,
    isPublic: workflow.is_public,
    requireLogin: workflow.require_login,
    shareUrl: workflow.share_url ?? null,
    chatkitStartScreenPrompts: workflow.chatkit?.start_screen_prompts ?? null,
    chatkitSupportedModels: workflow.chatkit?.supported_models ?? null,
  };
};
