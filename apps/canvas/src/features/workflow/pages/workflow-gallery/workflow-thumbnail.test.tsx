import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";

import { WorkflowThumbnail } from "./workflow-thumbnail";

const themeMock = vi.hoisted(() => ({
  useColorScheme: vi.fn(() => "light" as const),
}));

const workflowDataMock = vi.hoisted(() => ({
  getWorkflowTemplateDefinition: vi.fn(),
}));

const mermaidRendererMock = vi.hoisted(() => ({
  buildMermaidCacheKey: vi.fn(),
  buildMermaidRenderId: vi.fn(),
  forceMermaidLeftToRight: vi.fn((source: string) =>
    source.replace(/\b(?:TB|TD|BT|RL|LR)\b/, "LR"),
  ),
  normalizeMermaidPalette: vi.fn((source: string) =>
    source
      .replace(
        /classDef default fill:#eef4ff/g,
        "classDef default fill:#f2f0ff",
      )
      .replace(/classDef last fill:#94b8ff/g, "classDef last fill:#bfb6fc"),
  ),
  makeMermaidSvgTransparent: vi.fn((svg: string) => svg),
  renderMermaidSvg: vi.fn(),
}));

vi.mock("@features/workflow/data/workflow-data", async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import("@features/workflow/data/workflow-data")
    >();
  return {
    ...actual,
    getWorkflowTemplateDefinition:
      workflowDataMock.getWorkflowTemplateDefinition,
  };
});

vi.mock("@/hooks/use-color-scheme", () => ({
  useColorScheme: themeMock.useColorScheme,
}));

vi.mock("@features/workflow/lib/mermaid-renderer", () => ({
  buildMermaidCacheKey: mermaidRendererMock.buildMermaidCacheKey,
  buildMermaidRenderId: mermaidRendererMock.buildMermaidRenderId,
  forceMermaidLeftToRight: mermaidRendererMock.forceMermaidLeftToRight,
  normalizeMermaidPalette: mermaidRendererMock.normalizeMermaidPalette,
  makeMermaidSvgTransparent: mermaidRendererMock.makeMermaidSvgTransparent,
  renderMermaidSvg: mermaidRendererMock.renderMermaidSvg,
}));

const baseWorkflow = {
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
} satisfies Parameters<typeof WorkflowThumbnail>[0]["workflow"];

afterEach(() => {
  cleanup();
  themeMock.useColorScheme.mockReturnValue("light");
  workflowDataMock.getWorkflowTemplateDefinition.mockReset();
  mermaidRendererMock.buildMermaidCacheKey.mockReset();
  mermaidRendererMock.buildMermaidRenderId.mockReset();
  mermaidRendererMock.forceMermaidLeftToRight.mockClear();
  mermaidRendererMock.normalizeMermaidPalette.mockClear();
  mermaidRendererMock.makeMermaidSvgTransparent.mockClear();
  mermaidRendererMock.renderMermaidSvg.mockReset();
});

describe("WorkflowThumbnail", () => {
  it("uses fallback thumbnail when workflow has no Mermaid source", () => {
    const { container } = render(<WorkflowThumbnail workflow={baseWorkflow} />);

    expect(
      container.querySelector(".workflow-thumbnail-fallback"),
    ).not.toBeNull();
    expect(container.querySelector(".workflow-thumbnail-mermaid")).toBeNull();
    expect(mermaidRendererMock.renderMermaidSvg).not.toHaveBeenCalled();
  });

  it("switches the fallback palette with the canvas theme", () => {
    themeMock.useColorScheme.mockReturnValue("dark");

    const { container } = render(
      <WorkflowThumbnail
        workflow={{
          ...baseWorkflow,
          nodes: [
            {
              id: "trigger-1",
              type: "trigger",
              position: { x: 0, y: 0 },
              data: { label: "Trigger" },
            },
          ],
        }}
      />,
    );

    const rect = container.querySelector(".workflow-thumbnail-fallback rect");
    expect(rect).not.toBeNull();
    expect(rect?.getAttribute("fill")).toBe("#fbbf24");
    expect(rect?.getAttribute("stroke")).toBe("#f59e0b");
  });

  it("renders Mermaid thumbnail when latest version contains Mermaid source", async () => {
    mermaidRendererMock.buildMermaidCacheKey.mockReturnValue("cache-key");
    mermaidRendererMock.buildMermaidRenderId.mockReturnValue("render-id");
    mermaidRendererMock.renderMermaidSvg.mockResolvedValue(
      '<svg data-testid="mermaid-preview"></svg>',
    );

    const workflowWithMermaid = {
      ...baseWorkflow,
      versions: [
        {
          id: "v1",
          mermaid: "flowchart TD\nA[Start] --> B[End]",
        },
      ],
    };

    const { container } = render(
      <WorkflowThumbnail workflow={workflowWithMermaid} />,
    );

    await waitFor(() => {
      expect(mermaidRendererMock.renderMermaidSvg).toHaveBeenCalledWith({
        source: "flowchart LR\nA[Start] --> B[End]",
        cacheKey: "cache-key",
        renderId: "render-id",
        transformSvg: mermaidRendererMock.makeMermaidSvgTransparent,
      });
    });
    expect(mermaidRendererMock.forceMermaidLeftToRight).toHaveBeenCalledWith(
      "flowchart TD\nA[Start] --> B[End]",
    );

    await waitFor(() => {
      expect(
        container.querySelector(".workflow-thumbnail-mermaid svg"),
      ).not.toBeNull();
    });

    const thumbnailRoot = container.querySelector(
      ".workflow-thumbnail-mermaid",
    );
    expect(thumbnailRoot?.className).toContain("items-center");
    expect(thumbnailRoot?.className).toContain("justify-center");

    expect(container.querySelector(".workflow-thumbnail-fallback")).toBeNull();
  });

  it("shows loading placeholder instead of fallback while Mermaid is rendering", () => {
    mermaidRendererMock.buildMermaidCacheKey.mockReturnValue("cache-key");
    mermaidRendererMock.buildMermaidRenderId.mockReturnValue("render-id");
    mermaidRendererMock.renderMermaidSvg.mockImplementation(
      () =>
        new Promise(() => {
          // Keep promise pending to validate the loading state.
        }),
    );

    const workflowWithMermaid = {
      ...baseWorkflow,
      versions: [
        {
          id: "v1",
          mermaid: "flowchart TD\nA[Start] --> B[End]",
        },
      ],
    };

    const { container } = render(
      <WorkflowThumbnail workflow={workflowWithMermaid} />,
    );

    expect(
      container.querySelector(".workflow-thumbnail-loading"),
    ).not.toBeNull();
    expect(container.querySelector(".workflow-thumbnail-fallback")).toBeNull();
  });

  it("preserves the saved Mermaid preview for template-derived workflows", async () => {
    mermaidRendererMock.buildMermaidCacheKey.mockReturnValue("cache-key");
    mermaidRendererMock.buildMermaidRenderId.mockReturnValue("render-id");
    mermaidRendererMock.renderMermaidSvg.mockResolvedValue(
      '<svg data-testid="saved-preview"></svg>',
    );
    workflowDataMock.getWorkflowTemplateDefinition.mockReturnValue({
      workflow: {
        ...baseWorkflow,
        versions: [
          {
            id: "template-v1",
            mermaid: [
              "flowchart TD",
              "Template --> Preview",
              "classDef default fill:#eef4ff,line-height:1.2",
              "classDef last fill:#94b8ff",
            ].join("\n"),
          },
        ],
      },
      script: "",
      notes: "",
    });

    render(
      <WorkflowThumbnail
        workflow={{
          ...baseWorkflow,
          versions: [
            {
              id: "saved-v1",
              mermaid: "flowchart TD\nSaved --> Workflow",
              templateId: "template-python-agent",
            },
          ],
        }}
      />,
    );

    await waitFor(() => {
      expect(mermaidRendererMock.renderMermaidSvg).toHaveBeenCalledWith({
        source: "flowchart LR\nSaved --> Workflow",
        cacheKey: "cache-key",
        renderId: "render-id",
        transformSvg: mermaidRendererMock.makeMermaidSvgTransparent,
      });
    });
  });

  it("falls back to the template Mermaid preview when the saved version has none", async () => {
    mermaidRendererMock.buildMermaidCacheKey.mockReturnValue("cache-key");
    mermaidRendererMock.buildMermaidRenderId.mockReturnValue("render-id");
    mermaidRendererMock.renderMermaidSvg.mockResolvedValue(
      '<svg data-testid="template-preview"></svg>',
    );
    workflowDataMock.getWorkflowTemplateDefinition.mockReturnValue({
      workflow: {
        ...baseWorkflow,
        versions: [
          {
            id: "template-v1",
            mermaid: [
              "flowchart TD",
              "Template --> Preview",
              "classDef default fill:#eef4ff,line-height:1.2",
              "classDef last fill:#94b8ff",
            ].join("\n"),
          },
        ],
      },
      script: "",
      notes: "",
    });

    render(
      <WorkflowThumbnail
        workflow={{
          ...baseWorkflow,
          versions: [
            {
              id: "saved-v1",
              templateId: "template-python-agent",
            },
          ],
        }}
      />,
    );

    await waitFor(() => {
      expect(mermaidRendererMock.renderMermaidSvg).toHaveBeenCalledWith({
        source: [
          "flowchart LR",
          "Template --> Preview",
          "classDef default fill:#f2f0ff,line-height:1.2",
          "classDef last fill:#bfb6fc",
        ].join("\n"),
        cacheKey: "cache-key",
        renderId: "render-id",
        transformSvg: mermaidRendererMock.makeMermaidSvgTransparent,
      });
    });
  });
});
