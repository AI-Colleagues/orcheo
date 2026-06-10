import { SAMPLE_WORKFLOWS } from "@features/workflow/data/workflow-data";

import {
  jsonResponse,
  parseRequestBody,
} from "@/testing/mocks/backend/request-utils";

export type WorkflowRecord = {
  workflow: {
    id: string;
    handle?: string | null;
    name: string;
    slug: string;
    description: string | null;
    tags: string[];
    is_archived: boolean;
    created_at: string;
    updated_at: string;
  };
  versions: Array<{
    id: string;
    workflow_id: string;
    version: number;
    graph: Record<string, unknown>;
    metadata: unknown;
    notes: string | null;
    created_by: string;
    created_at: string;
    updated_at: string;
  }>;
};

const workflowStore = new Map<string, WorkflowRecord>();

let workflowCounter = 0;

const slugify = (value: string) =>
  value
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");

const findWorkflowRecord = (
  workflowRef: string,
): [string, WorkflowRecord] | undefined => {
  const direct = workflowStore.get(workflowRef);
  if (direct) {
    return [workflowRef, direct];
  }

  for (const [workflowId, record] of workflowStore.entries()) {
    if (record.workflow.handle === workflowRef) {
      return [workflowId, record];
    }
  }

  return undefined;
};

export const seedWorkflows = () => {
  if (workflowStore.size > 0) {
    return;
  }

  SAMPLE_WORKFLOWS.slice(0, 3).forEach((sample, index) => {
    const id = `mock-workflow-${index + 1}`;
    const createdAt = sample.createdAt ?? new Date().toISOString();
    const updatedAt = sample.updatedAt ?? createdAt;

    workflowStore.set(id, {
      workflow: {
        id,
        handle: null,
        name: sample.name,
        slug: slugify(sample.name),
        description: sample.description ?? null,
        tags: [...sample.tags],
        is_archived: false,
        created_at: createdAt,
        updated_at: updatedAt,
      },
      versions: [
        {
          id: `${id}-version-1`,
          workflow_id: id,
          version: 1,
          graph: {},
          metadata: {
            canvas: {
              snapshot: {
                name: sample.name,
                description: sample.description ?? null,
                nodes: sample.nodes ?? [],
                edges: sample.edges ?? [],
              },
              summary: { added: 0, removed: 0, modified: 0 },
              message: "Initial version",
              canvasToGraph: {},
              graphToCanvas: {},
            },
          },
          notes: "Initial version",
          created_by: "canvas-app",
          created_at: updatedAt,
          updated_at: updatedAt,
        },
      ],
    });
  });

  workflowCounter = workflowStore.size;
};

export const handleWorkflowRequest = async (
  request: Request,
  url: URL,
): Promise<Response> => {
  const segments = url.pathname.split("/").filter(Boolean);
  const method = request.method.toUpperCase();

  if (segments.length === 2) {
    if (method === "GET") {
      return jsonResponse(
        Array.from(workflowStore.values()).map((entry) => entry.workflow),
      );
    }

    if (method === "POST") {
      const payload = await parseRequestBody<{
        handle?: string | null;
        name?: string;
        description?: string | null;
        tags?: string[];
        actor?: string;
      }>(request);

      const now = new Date().toISOString();
      const id = `mock-workflow-${++workflowCounter}`;

      const workflow: WorkflowRecord["workflow"] = {
        id,
        handle: payload?.handle ?? null,
        name: payload?.name ?? `Workflow ${workflowCounter}`,
        slug: slugify(payload?.name ?? `Workflow ${workflowCounter}`),
        description: payload?.description ?? null,
        tags: payload?.tags ?? [],
        is_archived: false,
        created_at: now,
        updated_at: now,
      };

      workflowStore.set(id, {
        workflow,
        versions: [],
      });

      return jsonResponse(workflow, { status: 201 });
    }
  }

  if (segments.length >= 3) {
    const workflowRef = segments[2];
    const resolved = findWorkflowRecord(workflowRef);
    const workflowId = resolved?.[0] ?? workflowRef;
    const record = resolved?.[1];

    if (!record) {
      return jsonResponse(
        { detail: `Workflow ${workflowId} not found` },
        { status: 404 },
      );
    }

    if (segments.length === 3) {
      if (method === "GET") {
        return jsonResponse(record.workflow);
      }

      if (method === "PUT") {
        const payload = await parseRequestBody<{
          handle?: string | null;
          name?: string;
          description?: string | null;
          tags?: string[];
        }>(request);

        const now = new Date().toISOString();
        record.workflow = {
          ...record.workflow,
          handle:
            payload?.handle !== undefined
              ? payload.handle
              : record.workflow.handle,
          name: payload?.name ?? record.workflow.name,
          slug: slugify(payload?.name ?? record.workflow.name),
          description:
            payload?.description !== undefined
              ? payload.description
              : record.workflow.description,
          tags: payload?.tags ?? record.workflow.tags,
          updated_at: now,
        };

        workflowStore.set(workflowId, record);
        return jsonResponse(record.workflow);
      }
    }

    if (segments.length === 4 && segments[3] === "versions") {
      if (method === "GET") {
        return jsonResponse(record.versions);
      }

      if (method === "POST") {
        const payload = await parseRequestBody<{
          graph?: Record<string, unknown>;
          metadata?: unknown;
          notes?: string | null;
          created_by?: string;
        }>(request);
        const nextVersionNumber = record.versions.length + 1;
        const now = new Date().toISOString();

        const version = {
          id: `${workflowId}-version-${nextVersionNumber}`,
          workflow_id: workflowId,
          version: nextVersionNumber,
          graph: payload?.graph ?? {},
          metadata: payload?.metadata ?? {
            canvas: {
              snapshot: {
                name: record.workflow.name,
                description: record.workflow.description,
                nodes: [],
                edges: [],
              },
              summary: { added: 0, removed: 0, modified: 0 },
              message: payload?.notes ?? null,
              canvasToGraph: {},
              graphToCanvas: {},
            },
          },
          notes: payload?.notes ?? null,
          created_by: payload?.created_by ?? "canvas-app",
          created_at: now,
          updated_at: now,
        };

        record.workflow.updated_at = now;
        record.versions.push(version);
        workflowStore.set(workflowId, record);

        return jsonResponse(version, { status: 201 });
      }
    }

    if (
      segments.length === 5 &&
      segments[3] === "versions" &&
      segments[4] === "ingest" &&
      method === "POST"
    ) {
      const payload = await parseRequestBody<{
        script?: string;
        entrypoint?: string | null;
        metadata?: unknown;
        notes?: string | null;
        created_by?: string;
      }>(request);

      if (!payload?.script || typeof payload.script !== "string") {
        return jsonResponse(
          { detail: "script is required for ingest endpoint" },
          { status: 400 },
        );
      }

      const nextVersionNumber = record.versions.length + 1;
      const now = new Date().toISOString();
      const version = {
        id: `${workflowId}-version-${nextVersionNumber}`,
        workflow_id: workflowId,
        version: nextVersionNumber,
        graph: {
          format: "langgraph-script",
          source: payload.script,
          entrypoint: payload.entrypoint ?? null,
          index: { cron: [] },
        },
        metadata: payload.metadata ?? {},
        notes: payload.notes ?? null,
        created_by: payload.created_by ?? "canvas-app",
        created_at: now,
        updated_at: now,
      };

      record.workflow.updated_at = now;
      record.versions.push(version);
      workflowStore.set(workflowId, record);

      return jsonResponse(version, { status: 201 });
    }
  }

  return jsonResponse(
    { detail: "Not implemented in test stub" },
    { status: 404 },
  );
};
