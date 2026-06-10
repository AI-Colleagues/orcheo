import { beforeEach, describe, expect, it, vi } from "vitest";
import { type Workflow } from "@features/workflow/data/workflow-data";
import { getWorkflowTemplateDefinition } from "@features/workflow/data/workflow-data";
import { fetchWorkflowVersions } from "@features/workflow/lib/workflow-storage-api";
import { resolveWorkflowPythonSource } from "./use-workflow-gallery-actions";

vi.mock("@features/workflow/data/workflow-data", async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import("@features/workflow/data/workflow-data")
    >();
  return {
    ...actual,
    getWorkflowTemplateDefinition: vi.fn(),
  };
});

vi.mock(
  "@features/workflow/lib/workflow-storage-api",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("@features/workflow/lib/workflow-storage-api")
      >();
    return {
      ...actual,
      fetchWorkflowVersions: vi.fn(),
    };
  },
);

const workflow: Workflow = {
  id: "workflow-1",
  name: "Support triage",
  description: "Routes inbound requests.",
  createdAt: "2026-01-01T00:00:00.000Z",
  updatedAt: "2026-01-02T00:00:00.000Z",
  owner: {
    id: "owner-1",
    name: "Owner",
    avatar: "https://example.com/avatar.png",
  },
  tags: ["support", "triage"],
  nodes: [],
  edges: [],
};

const mockedGetWorkflowTemplateDefinition = vi.mocked(
  getWorkflowTemplateDefinition,
);
const mockedFetchWorkflowVersions = vi.mocked(fetchWorkflowVersions);

describe("resolveWorkflowPythonSource", () => {
  beforeEach(() => {
    mockedGetWorkflowTemplateDefinition.mockReset();
    mockedFetchWorkflowVersions.mockReset();
  });

  it("returns template script when workflow matches a built-in template", async () => {
    mockedGetWorkflowTemplateDefinition.mockReturnValue({
      workflow,
      script: "def orcheo_workflow():\n    return None\n",
      notes: "Template export",
    });

    const source = await resolveWorkflowPythonSource(workflow);

    expect(source).toContain("def orcheo_workflow");
    expect(mockedFetchWorkflowVersions).not.toHaveBeenCalled();
  });

  it("returns latest LangGraph source from workflow versions", async () => {
    mockedGetWorkflowTemplateDefinition.mockReturnValue(undefined);
    mockedFetchWorkflowVersions.mockResolvedValue([
      {
        id: "v1",
        workflow_id: workflow.id,
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "print('old')\n",
        },
        metadata: {},
        runnable_config: null,
        notes: null,
        created_by: "owner-1",
        created_at: "2026-01-01T00:00:00.000Z",
        updated_at: "2026-01-01T00:00:00.000Z",
      },
      {
        id: "v2",
        workflow_id: workflow.id,
        version: 2,
        graph: {
          format: "langgraph-script",
          source: "print('new')\n",
        },
        metadata: {},
        runnable_config: null,
        notes: null,
        created_by: "owner-1",
        created_at: "2026-01-02T00:00:00.000Z",
        updated_at: "2026-01-02T00:00:00.000Z",
      },
    ]);

    const source = await resolveWorkflowPythonSource(workflow);

    expect(source).toBe("print('new')\n");
  });

  it("throws when latest version is not a LangGraph script", async () => {
    mockedGetWorkflowTemplateDefinition.mockReturnValue(undefined);
    mockedFetchWorkflowVersions.mockResolvedValue([
      {
        id: "v1",
        workflow_id: workflow.id,
        version: 1,
        graph: {
          format: "json",
          source: "{}",
        },
        metadata: {},
        runnable_config: null,
        notes: null,
        created_by: "owner-1",
        created_at: "2026-01-01T00:00:00.000Z",
        updated_at: "2026-01-01T00:00:00.000Z",
      },
    ]);

    await expect(resolveWorkflowPythonSource(workflow)).rejects.toThrow(
      "unsupported format",
    );
  });
});
