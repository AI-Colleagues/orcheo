import type { Edge as CanvasEdge, Node as CanvasNode } from "@xyflow/react";
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
  CanvasVersionMetadata,
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

export const toCanvasNodes = (nodes: WorkflowNode[]): CanvasNode[] =>
  nodes.map(
    (node) =>
      ({
        id: node.id,
        type: node.type,
        position: node.position,
        data: node.data,
      }) satisfies CanvasNode,
  );

export const toCanvasEdges = (edges: WorkflowEdge[]): CanvasEdge[] =>
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
      }) satisfies CanvasEdge,
  );

export const getWorkflowRouteRef = (
  workflow:
    | Pick<ApiWorkflow, "id" | "handle">
    | Pick<Workflow, "id" | "handle">,
): string => workflow.handle ?? workflow.id;

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

const parseCanvasMetadata = (
  metadata: unknown,
  fallbackName: string,
  fallbackDescription?: string,
): CanvasVersionMetadata => {
  const configurableSchemas = parseConfigurableSchemas(
    isRecord(metadata) ? metadata.configurable_schema : undefined,
  );
  const resolveTemplateFallback = (): CanvasVersionMetadata | undefined => {
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
    };
  };

  if (!metadata || typeof metadata !== "object") {
    return {
      snapshot: emptySnapshot(fallbackName, fallbackDescription),
      summary: { ...DEFAULT_SUMMARY },
      templateId: undefined,
      configurableSchemas: undefined,
    };
  }

  const canvas = (metadata as Record<string, unknown>).canvas;
  if (!canvas || typeof canvas !== "object") {
    return (
      resolveTemplateFallback() ?? {
        snapshot: emptySnapshot(fallbackName, fallbackDescription),
        summary: { ...DEFAULT_SUMMARY },
        configurableSchemas,
      }
    );
  }

  const canvasRecord = canvas as Record<string, unknown>;
  const snapshotPayload = canvasRecord.snapshot as WorkflowSnapshot | undefined;
  const summaryPayload = canvasRecord.summary as
    | WorkflowDiffResult["summary"]
    | undefined;
  const messagePayload = canvasRecord.message as string | undefined;
  const canvasToGraph = canvasRecord.canvasToGraph as
    | Record<string, string>
    | undefined;
  const graphToCanvas = canvasRecord.graphToCanvas as
    | Record<string, string>
    | undefined;
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
    canvasToGraph,
    graphToCanvas,
    templateId,
    configurableSchemas,
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
  const metadata = parseCanvasMetadata(
    version.metadata,
    workflowName,
    workflowDescription ?? undefined,
  );

  const message =
    metadata.message ??
    version.notes ??
    `Updated from canvas on ${new Date(version.created_at).toLocaleString()}`;

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
    graphToCanvas: metadata.graphToCanvas,
    templateId: metadata.templateId,
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

  return {
    id: workflow.id,
    handle: workflow.handle ?? undefined,
    name: workflow.name,
    description: workflow.description ?? undefined,
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
    shareUrl: workflow.share_url ?? null,
    chatkitStartScreenPrompts: workflow.chatkit?.start_screen_prompts ?? null,
    chatkitSupportedModels: workflow.chatkit?.supported_models ?? null,
  };
};
