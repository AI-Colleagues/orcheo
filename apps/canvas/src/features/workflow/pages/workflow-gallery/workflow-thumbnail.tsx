import { useEffect, useMemo, useRef, useState } from "react";
import { type Workflow } from "@features/workflow/data/workflow-data";
import { useColorScheme } from "@/hooks/use-color-scheme";
import { cn } from "@/lib/utils";
import {
  buildMermaidCacheKey,
  buildMermaidRenderId,
  makeMermaidSvgTransparent,
  renderMermaidSvg,
} from "@features/workflow/lib/mermaid-renderer";
import { resolveWorkflowVersionMermaidSource } from "@features/workflow/lib/workflow-storage-helpers";

type ThumbnailPalette = {
  nodeFill: Record<string, string>;
  nodeStroke: Record<string, string>;
  edgeStroke: string;
  backgroundClassName: string;
};

const THUMBNAIL_PALETTE: Record<"light" | "dark", ThumbnailPalette> = {
  light: {
    nodeFill: {
      trigger: "#f59e0b",
      api: "#3b82f6",
      function: "#8b5cf6",
      data: "#10b981",
      ai: "#6366f1",
      python: "#f97316",
    },
    nodeStroke: {
      trigger: "#d97706",
      api: "#2563eb",
      function: "#7c3aed",
      data: "#059669",
      ai: "#4f46e5",
      python: "#ea580c",
    },
    edgeStroke: "#94a3b8",
    backgroundClassName: "from-background/90 via-muted/30 to-background/80",
  },
  dark: {
    nodeFill: {
      trigger: "#fbbf24",
      api: "#60a5fa",
      function: "#a78bfa",
      data: "#34d399",
      ai: "#818cf8",
      python: "#fb923c",
    },
    nodeStroke: {
      trigger: "#f59e0b",
      api: "#93c5fd",
      function: "#c4b5fd",
      data: "#6ee7b7",
      ai: "#a5b4fc",
      python: "#fdba74",
    },
    edgeStroke: "#cbd5e1",
    backgroundClassName: "from-slate-950 via-slate-900 to-slate-950",
  },
};

interface WorkflowThumbnailProps {
  workflow: Workflow;
}

export const WorkflowThumbnail = ({ workflow }: WorkflowThumbnailProps) => {
  const [diagramSvg, setDiagramSvg] = useState<string | null>(null);
  const [diagramError, setDiagramError] = useState<string | null>(null);
  const [hasEnteredViewport, setHasEnteredViewport] = useState(false);
  const colorScheme = useColorScheme();
  const containerRef = useRef<HTMLDivElement | null>(null);

  const latestVersion = workflow.versions?.at(-1);

  const mermaidSource = useMemo(() => {
    return resolveWorkflowVersionMermaidSource(latestVersion);
  }, [latestVersion]);

  const mermaidCacheKey = useMemo(() => {
    if (!mermaidSource) {
      return null;
    }

    return buildMermaidCacheKey({
      scope: "gallery-thumbnail",
      workflowId: workflow.id,
      versionId: latestVersion?.id ?? "latest",
      source: mermaidSource,
    });
  }, [latestVersion?.id, mermaidSource, workflow.id]);

  const renderId = useMemo(() => {
    if (!mermaidCacheKey) {
      return null;
    }

    return buildMermaidRenderId("workflow-gallery-mermaid", mermaidCacheKey);
  }, [mermaidCacheKey]);

  useEffect(() => {
    if (!mermaidSource) {
      setHasEnteredViewport(false);
      return;
    }

    const element = containerRef.current;
    if (
      !element ||
      typeof window === "undefined" ||
      typeof IntersectionObserver === "undefined"
    ) {
      setHasEnteredViewport(true);
      return;
    }

    const preloadMargin = 200;
    const rect = element.getBoundingClientRect();
    const isWithinPreloadRange =
      rect.bottom >= -preloadMargin &&
      rect.top <= window.innerHeight + preloadMargin;

    if (isWithinPreloadRange) {
      setHasEnteredViewport(true);
      return;
    }

    setHasEnteredViewport(false);
    const observer = new IntersectionObserver(
      (entries) => {
        const isIntersecting = entries.some(
          (entry) => entry.isIntersecting || entry.intersectionRatio > 0,
        );

        if (isIntersecting) {
          setHasEnteredViewport(true);
          observer.disconnect();
        }
      },
      { rootMargin: `${preloadMargin}px 0px` },
    );

    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [mermaidCacheKey, mermaidSource]);

  useEffect(() => {
    if (
      !mermaidSource ||
      !renderId ||
      !mermaidCacheKey ||
      !hasEnteredViewport
    ) {
      if (!mermaidSource) {
        setDiagramSvg(null);
        setDiagramError(null);
      }
      return;
    }

    setDiagramSvg(null);
    setDiagramError(null);

    let isMounted = true;

    const renderMermaidThumbnail = async () => {
      try {
        const svg = await renderMermaidSvg({
          source: mermaidSource,
          cacheKey: mermaidCacheKey,
          renderId,
          transformSvg: makeMermaidSvgTransparent,
        });
        if (!isMounted) {
          return;
        }
        setDiagramSvg(svg);
        setDiagramError(null);
      } catch (error) {
        if (!isMounted) {
          return;
        }
        setDiagramSvg(null);
        setDiagramError(
          error instanceof Error ? error.message : "Unable to render diagram.",
        );
      }
    };

    void renderMermaidThumbnail();

    return () => {
      isMounted = false;
    };
  }, [hasEnteredViewport, mermaidCacheKey, mermaidSource, renderId]);

  const showMermaidThumbnail = Boolean(
    mermaidSource && hasEnteredViewport && diagramSvg && !diagramError,
  );
  const showLoadingThumbnail = Boolean(
    mermaidSource && hasEnteredViewport && !diagramSvg && !diagramError,
  );
  const showFallbackThumbnail = Boolean(
    !mermaidSource || diagramError || !hasEnteredViewport,
  );
  const palette = THUMBNAIL_PALETTE[colorScheme];

  return (
    <div
      ref={containerRef}
      className={cn(
        "relative h-24 w-full overflow-hidden rounded-md border border-border/60 bg-gradient-to-br shadow-inner",
        palette.backgroundClassName,
      )}
    >
      {showMermaidThumbnail ? (
        <div
          className="workflow-thumbnail-mermaid absolute inset-0 flex items-center justify-center p-1 [&_svg]:block [&_svg]:max-h-full [&_svg]:max-w-full [&_svg]:!h-auto [&_svg]:!w-auto"
          dangerouslySetInnerHTML={{ __html: diagramSvg }}
        />
      ) : null}

      {showLoadingThumbnail ? (
        <div
          className="workflow-thumbnail-loading absolute inset-0 animate-pulse bg-muted/40 dark:bg-muted/20"
          aria-hidden="true"
        />
      ) : null}

      {showFallbackThumbnail ? (
        <svg
          width="100%"
          height="100%"
          viewBox="0 0 200 100"
          className="workflow-thumbnail-fallback absolute inset-0"
        >
          {workflow.nodes.slice(0, 5).map((node, index) => {
            const x = 30 + (index % 3) * 70;
            const y = 30 + Math.floor(index / 3) * 40;
            const fill = palette.nodeFill[node.type] ?? palette.edgeStroke;
            const stroke = palette.nodeStroke[node.type] ?? palette.edgeStroke;

            return (
              <g key={node.id}>
                <rect
                  x={x - 15}
                  y={y - 10}
                  width={30}
                  height={20}
                  rx={4}
                  fill={fill}
                  fillOpacity={0.24}
                  stroke={stroke}
                  strokeWidth={1}
                />
              </g>
            );
          })}

          {workflow.edges.slice(0, 4).map((edge) => {
            const sourceIndex = workflow.nodes.findIndex(
              (node) => node.id === edge.source,
            );
            const targetIndex = workflow.nodes.findIndex(
              (node) => node.id === edge.target,
            );

            if (
              sourceIndex < 0 ||
              targetIndex < 0 ||
              sourceIndex >= 5 ||
              targetIndex >= 5
            ) {
              return null;
            }

            const sourceX = 30 + (sourceIndex % 3) * 70 + 15;
            const sourceY = 30 + Math.floor(sourceIndex / 3) * 40;
            const targetX = 30 + (targetIndex % 3) * 70 - 15;
            const targetY = 30 + Math.floor(targetIndex / 3) * 40;

            return (
              <path
                key={edge.id}
                d={`M${sourceX},${sourceY} C${sourceX + 20},${sourceY} ${targetX - 20},${targetY} ${targetX},${targetY}`}
                stroke={palette.edgeStroke}
                strokeWidth={1}
                fill="none"
              />
            );
          })}
        </svg>
      ) : null}
    </div>
  );
};
