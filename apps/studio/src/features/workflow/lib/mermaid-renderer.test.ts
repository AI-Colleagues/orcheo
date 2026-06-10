import { afterEach, describe, expect, it, vi } from "vitest";
import {
  __resetMermaidRenderCacheForTests,
  buildMermaidCacheKey,
  buildMermaidRenderId,
  forceMermaidLeftToRight,
  normalizeMermaidPalette,
  renderMermaidSvg,
} from "./mermaid-renderer";

const mermaidMock = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(),
}));

vi.mock("mermaid", () => ({
  default: {
    initialize: mermaidMock.initialize,
    render: mermaidMock.render,
  },
}));

afterEach(() => {
  __resetMermaidRenderCacheForTests();
  mermaidMock.initialize.mockReset();
  mermaidMock.render.mockReset();
  vi.restoreAllMocks();
});

describe("mermaid-renderer", () => {
  it("normalizes flow direction to left-to-right", () => {
    expect(forceMermaidLeftToRight("flowchart TD\nA --> B")).toBe(
      "flowchart LR\nA --> B",
    );
    expect(forceMermaidLeftToRight("graph TD; A --> B")).toBe(
      "graph LR; A --> B",
    );
    expect(forceMermaidLeftToRight("flowchart\nA --> B")).toBe(
      "flowchart LR\nA --> B",
    );
  });

  it("normalizes template palette aliases to the default workflow palette", () => {
    expect(
      normalizeMermaidPalette(
        [
          "graph TD;",
          "\tclassDef default fill:#eef4ff,line-height:1.2",
          "\tclassDef last fill:#94b8ff",
        ].join("\n"),
      ),
    ).toContain("classDef default fill:#f2f0ff,line-height:1.2");
    expect(
      normalizeMermaidPalette(
        [
          "graph TD;",
          "\tclassDef default fill:#eef4ff,line-height:1.2",
          "\tclassDef last fill:#94b8ff",
        ].join("\n"),
      ),
    ).toContain("classDef last fill:#bfb6fc");
  });

  it("reuses cached svg for the same key", async () => {
    mermaidMock.render.mockResolvedValue({ svg: "<svg id='first'></svg>" });

    const source = "flowchart TD\nA --> B";
    const cacheKey = buildMermaidCacheKey({
      scope: "gallery-thumbnail",
      workflowId: "wf-1",
      versionId: "v1",
      source,
    });
    const renderId = buildMermaidRenderId("workflow-gallery-mermaid", cacheKey);

    const first = await renderMermaidSvg({ source, cacheKey, renderId });
    const second = await renderMermaidSvg({ source, cacheKey, renderId });

    expect(first).toBe("<svg id='first'></svg>");
    expect(second).toBe("<svg id='first'></svg>");
    expect(mermaidMock.initialize).toHaveBeenCalledTimes(1);
    expect(mermaidMock.render).toHaveBeenCalledTimes(1);
  });

  it("deduplicates in-flight renders for the same key", async () => {
    let resolveRender: ((value: { svg: string }) => void) | undefined;
    mermaidMock.render.mockImplementation(
      () =>
        new Promise<{ svg: string }>((resolve) => {
          resolveRender = resolve;
        }),
    );

    const source = "flowchart TD\nA --> C";
    const cacheKey = buildMermaidCacheKey({
      scope: "gallery-thumbnail",
      workflowId: "wf-2",
      versionId: "v1",
      source,
    });
    const renderId = buildMermaidRenderId("workflow-gallery-mermaid", cacheKey);

    const first = renderMermaidSvg({ source, cacheKey, renderId });
    const second = renderMermaidSvg({ source, cacheKey, renderId });

    await vi.waitFor(() => {
      expect(mermaidMock.render).toHaveBeenCalledTimes(1);
    });

    resolveRender?.({ svg: "<svg id='deduped'></svg>" });
    await expect(Promise.all([first, second])).resolves.toEqual([
      "<svg id='deduped'></svg>",
      "<svg id='deduped'></svg>",
    ]);
  });

  it("limits concurrent mermaid renders", async () => {
    let activeRenders = 0;
    let maxActiveRenders = 0;
    const releaseQueue: Array<() => void> = [];

    mermaidMock.render.mockImplementation(async () => {
      activeRenders += 1;
      maxActiveRenders = Math.max(maxActiveRenders, activeRenders);

      await new Promise<void>((resolve) => {
        releaseQueue.push(resolve);
      });

      activeRenders -= 1;
      return { svg: "<svg id='limited'></svg>" };
    });

    const requests = Array.from({ length: 6 }, (_, index) => {
      const source = `flowchart TD\nA --> N${index.toString()}`;
      const cacheKey = buildMermaidCacheKey({
        scope: "gallery-thumbnail",
        workflowId: "wf-concurrency",
        versionId: `v${index.toString()}`,
        source,
      });
      const renderId = buildMermaidRenderId(
        "workflow-gallery-mermaid",
        cacheKey,
      );
      return renderMermaidSvg({ source, cacheKey, renderId });
    });

    await vi.waitFor(() => {
      expect(mermaidMock.render).toHaveBeenCalledTimes(3);
    });

    releaseQueue.splice(0, 3).forEach((release) => release());

    await vi.waitFor(() => {
      expect(mermaidMock.render).toHaveBeenCalledTimes(6);
    });

    releaseQueue.splice(0).forEach((release) => release());
    await Promise.all(requests);

    expect(maxActiveRenders).toBeLessThanOrEqual(3);
  });

  it("logs when session cache hydration fails", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const storagePrototype = Object.getPrototypeOf(window.sessionStorage) as {
      getItem: (key: string) => string | null;
    };
    vi.spyOn(storagePrototype, "getItem").mockImplementation(() => {
      throw new Error("read failed");
    });
    mermaidMock.render.mockResolvedValue({
      svg: "<svg id='read-fallback'></svg>",
    });

    const source = "flowchart TD\nA --> Read";
    const cacheKey = buildMermaidCacheKey({
      scope: "gallery-thumbnail",
      workflowId: "wf-read-failure",
      versionId: "v1",
      source,
    });
    const renderId = buildMermaidRenderId("workflow-gallery-mermaid", cacheKey);

    await expect(
      renderMermaidSvg({ source, cacheKey, renderId }),
    ).resolves.toBe("<svg id='read-fallback'></svg>");
    expect(warnSpy).toHaveBeenCalledWith(
      "Failed to read Mermaid SVG session cache",
      expect.any(Error),
    );
  });

  it("logs when session cache persistence fails", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const storagePrototype = Object.getPrototypeOf(window.sessionStorage) as {
      setItem: (key: string, value: string) => void;
    };
    vi.spyOn(storagePrototype, "setItem").mockImplementation(() => {
      throw new Error("write failed");
    });
    mermaidMock.render.mockResolvedValue({
      svg: "<svg id='write-fallback'></svg>",
    });

    const source = "flowchart TD\nA --> Write";
    const cacheKey = buildMermaidCacheKey({
      scope: "gallery-thumbnail",
      workflowId: "wf-write-failure",
      versionId: "v1",
      source,
    });
    const renderId = buildMermaidRenderId("workflow-gallery-mermaid", cacheKey);

    await expect(
      renderMermaidSvg({ source, cacheKey, renderId }),
    ).resolves.toBe("<svg id='write-fallback'></svg>");
    expect(warnSpy).toHaveBeenCalledWith(
      "Failed to write Mermaid SVG session cache",
      expect.any(Error),
    );
  });
});
