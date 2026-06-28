import { useEffect, useMemo, useState } from "react";
import { useUploadsAllowed } from "@/hooks/use-uploads-allowed";
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  ExternalLink,
  LoaderCircle,
  Play,
  RefreshCw,
  UserMinus,
  XCircle,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Controls, ReactFlow, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { Alert, AlertDescription, AlertTitle } from "@/design-system/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/design-system/ui/alert-dialog";
import { Button } from "@/design-system/ui/button";
import { Switch } from "@/design-system/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/design-system/ui/tooltip";
import { toast } from "@/hooks/use-toast";
import { ConfirmDeleteWorkflowDialog } from "@features/workflow/components/dialogs/confirm-delete-workflow-dialog";
import { PublishWorkflowDialog } from "@features/workflow/components/dialogs/publish-workflow-dialog";
import { UpdateWorkflowDialog } from "@features/workflow/components/dialogs/update-workflow-dialog";
import { deleteWorkflow } from "@features/workflow/lib/workflow-storage";
import {
  fetchCronTriggerConfig,
  publishWorkflow,
  scheduleWorkflowFromLatestVersion,
  unpublishWorkflow,
  unscheduleWorkflow,
} from "@features/workflow/lib/workflow-storage-api";
import type {
  WorkflowRunnableConfig,
  WorkflowVersionRecord,
} from "@features/workflow/lib/workflow-storage.types";
import {
  buildMermaidCacheKey,
  buildMermaidRenderId,
  makeMermaidSvgTransparent,
  renderMermaidSvg,
} from "@features/workflow/lib/mermaid-renderer";
import { resolveWorkflowVersionMermaidSource } from "@features/workflow/lib/workflow-storage-helpers";
import { WorkflowConfigSheet } from "@features/workflow/pages/workflow/components/workflow-config-sheet";
import type { WorkflowExecutionStatus } from "@features/workflow/pages/workflow/helpers/types";

export interface WorkflowTabContentProps {
  workflowId: string | null;
  workflowRouteRef?: string | null;
  workflowName: string;
  versions: WorkflowVersionRecord[];
  isLoading: boolean;
  loadError: string | null;
  isRunPending: boolean;
  isRunning: boolean;
  lastRunStatus?: WorkflowExecutionStatus | null;
  onRunWorkflow: () => Promise<void>;
  onSaveConfig: (nextConfig: WorkflowRunnableConfig | null) => Promise<void>;
  hasCronTriggerNode: boolean;
  initialIsPublished: boolean;
  initialRequireLogin: boolean;
  initialShareUrl: string | null;
  missingCredentials?: string[];
}

interface MermaidSvgNodeData {
  svg: string;
  width: number;
  height: number;
}

const defaultMermaid = "flowchart LR\n  START([Start]) --> END([End])";
const DEFAULT_SVG_SIZE = { width: 960, height: 560 };
const MIN_SVG_WIDTH = 320;
const MIN_SVG_HEIGHT = 220;
const MAX_SVG_WIDTH = 2400;
const MAX_SVG_HEIGHT = 1800;

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const parseSvgDimension = (rawValue: string | undefined): number | null => {
  if (!rawValue) {
    return null;
  }

  const match = rawValue.match(/-?\d*\.?\d+/);
  if (!match) {
    return null;
  }

  const parsed = Number.parseFloat(match[0]);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};

const resolveSvgSize = (svg: string) => {
  const viewBoxMatch = svg.match(/\bviewBox\s*=\s*"([^"]+)"/i);
  if (viewBoxMatch) {
    const values = viewBoxMatch[1]
      .trim()
      .split(/[\s,]+/)
      .map((value) => Number.parseFloat(value));

    if (
      values.length === 4 &&
      values.every((value) => Number.isFinite(value)) &&
      values[2] > 0 &&
      values[3] > 0
    ) {
      return {
        width: clamp(values[2], MIN_SVG_WIDTH, MAX_SVG_WIDTH),
        height: clamp(values[3], MIN_SVG_HEIGHT, MAX_SVG_HEIGHT),
      };
    }
  }

  const width = parseSvgDimension(svg.match(/\bwidth\s*=\s*"([^"]+)"/i)?.[1]);
  const height = parseSvgDimension(svg.match(/\bheight\s*=\s*"([^"]+)"/i)?.[1]);

  if (width && height) {
    return {
      width: clamp(width, MIN_SVG_WIDTH, MAX_SVG_WIDTH),
      height: clamp(height, MIN_SVG_HEIGHT, MAX_SVG_HEIGHT),
    };
  }

  return DEFAULT_SVG_SIZE;
};

const MermaidSvgNode = ({ data }: NodeProps<Node<MermaidSvgNodeData>>) => {
  const nodeData = data as MermaidSvgNodeData;

  return (
    <div className="p-1">
      <div
        className="workflow-mermaid-svg pointer-events-none [&_svg]:block [&_svg]:h-full [&_svg]:w-full [&_svg]:max-w-none"
        style={{ width: nodeData.width, height: nodeData.height }}
        dangerouslySetInnerHTML={{ __html: nodeData.svg }}
      />
    </div>
  );
};

const nodeTypes = {
  mermaidSvg: MermaidSvgNode,
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (!(error instanceof Error)) {
    return fallback;
  }

  const rawMessage = error.message.trim();
  if (!rawMessage) {
    return fallback;
  }

  try {
    const parsed = JSON.parse(rawMessage);
    if (!isRecord(parsed)) {
      return rawMessage;
    }
    const detail = parsed.detail;
    if (typeof detail === "string" && detail.trim().length > 0) {
      return detail;
    }
    if (isRecord(detail) && typeof detail.message === "string") {
      return detail.message;
    }
  } catch {
    return rawMessage;
  }

  return rawMessage;
};

interface RunResultBanner {
  Icon: typeof CheckCircle2;
  className: string;
  message: string;
}

const resolveRunResultBanner = (
  status: WorkflowExecutionStatus,
): RunResultBanner => {
  switch (status) {
    case "success":
      return {
        Icon: CheckCircle2,
        className:
          "border-emerald-500/40 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
        message: "Workflow run succeeded. See the full trace on the Trace tab.",
      };
    case "failed":
      return {
        Icon: XCircle,
        className: "border-destructive/40 bg-destructive/5 text-destructive",
        message: "Workflow run failed. Check the Trace tab for details.",
      };
    case "partial":
      return {
        Icon: AlertTriangle,
        className:
          "border-amber-500/40 bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
        message:
          "Workflow run finished with partial results. Check the Trace tab for details.",
      };
    default:
      // "running" reaching here means the stream ended without a final status.
      return {
        Icon: AlertTriangle,
        className:
          "border-amber-500/40 bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
        message:
          "Workflow run ended without a final status. Check the Trace tab for the latest status.",
      };
  }
};

export function WorkflowTabContent({
  workflowId,
  workflowName,
  versions,
  isLoading,
  loadError,
  isRunPending,
  isRunning,
  lastRunStatus = null,
  onRunWorkflow,
  onSaveConfig,
  hasCronTriggerNode,
  initialIsPublished,
  initialRequireLogin,
  initialShareUrl,
  missingCredentials = [],
}: WorkflowTabContentProps) {
  const navigate = useNavigate();
  const uploadsAllowed = useUploadsAllowed();
  const latestVersion = versions.at(-1);
  const [isConfigOpen, setIsConfigOpen] = useState(false);
  const [isPublished, setIsPublished] = useState(initialIsPublished);
  const [requireLogin, setRequireLogin] = useState(initialRequireLogin);
  const [isScheduled, setIsScheduled] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(initialShareUrl);
  const [isPublishPending, setIsPublishPending] = useState(false);
  const [isPublishDialogOpen, setIsPublishDialogOpen] = useState(false);
  const [isSchedulePending, setIsSchedulePending] = useState(false);
  const [diagramSvg, setDiagramSvg] = useState<string | null>(null);
  const [diagramError, setDiagramError] = useState<string | null>(null);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isDeletePending, setIsDeletePending] = useState(false);
  const [isUpdateDialogOpen, setIsUpdateDialogOpen] = useState(false);
  const [isMissingCredentialsDialogOpen, setIsMissingCredentialsDialogOpen] =
    useState(false);
  const hasMissingCredentials = missingCredentials.length > 0;
  const canDeleteWorkflow = Boolean(workflowId);

  useEffect(() => {
    if (!workflowId) {
      setIsPublished(false);
      setIsScheduled(false);
      setRequireLogin(false);
      setShareUrl(null);
      return;
    }
    setIsPublished(initialIsPublished);
    setRequireLogin(initialRequireLogin);
    setShareUrl(initialShareUrl);
  }, [initialIsPublished, initialRequireLogin, initialShareUrl, workflowId]);

  useEffect(() => {
    if (!workflowId) {
      setIsScheduled(false);
      return;
    }

    if (!hasCronTriggerNode) {
      setIsScheduled(false);
      return;
    }

    let isMounted = true;

    const loadWorkflowSchedule = async () => {
      try {
        const cronConfig = await fetchCronTriggerConfig(workflowId);
        if (!isMounted) {
          return;
        }
        setIsScheduled(Boolean(cronConfig));
      } catch (error) {
        if (!isMounted) {
          return;
        }
        toast({
          title: "Failed to load workflow state",
          description: getErrorMessage(
            error,
            "Unable to load publish/schedule status.",
          ),
          variant: "destructive",
        });
      }
    };

    void loadWorkflowSchedule();
    return () => {
      isMounted = false;
    };
  }, [hasCronTriggerNode, workflowId]);

  const mermaidSource = useMemo(() => {
    return resolveWorkflowVersionMermaidSource(latestVersion);
  }, [latestVersion]);

  const mermaidCacheKey = useMemo(() => {
    if (!mermaidSource) {
      return null;
    }

    return buildMermaidCacheKey({
      scope: "workflow-tab",
      workflowId: workflowId ?? "workflow",
      versionId: latestVersion?.id ?? "latest",
      source: mermaidSource,
    });
  }, [latestVersion?.id, mermaidSource, workflowId]);

  const mermaidRenderId = useMemo(() => {
    if (!mermaidCacheKey) {
      return null;
    }

    return buildMermaidRenderId("workflow-mermaid-svg", mermaidCacheKey);
  }, [mermaidCacheKey]);

  useEffect(() => {
    if (!mermaidSource || !mermaidCacheKey || !mermaidRenderId) {
      setDiagramSvg(null);
      setDiagramError(null);
      return;
    }

    let isMounted = true;

    const renderMermaid = async () => {
      try {
        const svg = await renderMermaidSvg({
          source: mermaidSource,
          cacheKey: mermaidCacheKey,
          renderId: mermaidRenderId,
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

    void renderMermaid();

    return () => {
      isMounted = false;
    };
  }, [mermaidCacheKey, mermaidRenderId, mermaidSource]);

  const diagramNodes = useMemo(() => {
    if (!diagramSvg) {
      return [] as Node[];
    }

    const size = resolveSvgSize(diagramSvg);

    return [
      {
        id: "mermaid-svg-root",
        type: "mermaidSvg",
        position: { x: 0, y: 0 },
        data: {
          svg: diagramSvg,
          width: size.width,
          height: size.height,
        },
        draggable: false,
        selectable: false,
      } satisfies Node,
    ];
  }, [diagramSvg]);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        Loading workflow visualization...
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          Failed to load workflow: {loadError}
        </div>
      </div>
    );
  }

  const canConfigure = Boolean(workflowId);
  const isRunActive = isRunPending || isRunning;
  const runResultBanner =
    !isRunActive && lastRunStatus
      ? resolveRunResultBanner(lastRunStatus)
      : null;
  const canRun = Boolean(workflowId && latestVersion);
  const runButtonDisabled = !canRun || isRunPending || isRunning;
  const runButtonLabel = isRunPending || isRunning ? "Running..." : "Run";
  const latestConfig = latestVersion?.runnableConfig ?? null;
  const canToggleSchedule = hasCronTriggerNode || isScheduled;

  const handleCopyShareUrl = async () => {
    if (!shareUrl) {
      return;
    }

    try {
      await navigator.clipboard.writeText(shareUrl);
      toast({
        title: "Public URL copied",
        description: "The workflow URL has been copied to your clipboard.",
      });
    } catch (error) {
      toast({
        title: "Failed to copy public URL",
        description: getErrorMessage(error, "Clipboard access is unavailable."),
        variant: "destructive",
      });
    }
  };

  const handlePublishToggle = async (nextValue: boolean) => {
    if (!workflowId) {
      setIsPublished(false);
      toast({
        title: "Save workflow first",
        description: "Publishing requires a saved workflow ID.",
        variant: "destructive",
      });
      return;
    }

    if (nextValue) {
      setIsPublishDialogOpen(true);
      return;
    }

    setIsPublishPending(true);
    try {
      await unpublishWorkflow(workflowId, "studio");
      setIsPublished(false);
      setRequireLogin(false);
      setShareUrl(null);
      toast({
        title: "Workflow unpublished",
        description: "Workflow is now private.",
      });
    } catch (error) {
      setIsPublished(true);
      toast({
        title: "Failed to unpublish workflow",
        description: getErrorMessage(error, "Unable to update publish status."),
        variant: "destructive",
      });
    } finally {
      setIsPublishPending(false);
    }
  };

  const handleConfirmPublish = async (requireLogin: boolean) => {
    if (!workflowId) {
      return;
    }

    setIsPublishPending(true);
    try {
      const result = await publishWorkflow(workflowId, {
        actor: "studio",
        requireLogin,
      });
      setIsPublished(true);
      setRequireLogin(result.workflow.require_login);
      setShareUrl(result.shareUrl);
      setIsPublishDialogOpen(false);
      toast({
        title: "Workflow published",
        description:
          result.message ??
          (requireLogin
            ? "Workflow is now available to signed-in workspace members."
            : "Workflow is now public and available via its chat URL."),
      });
    } catch (error) {
      setIsPublished(false);
      toast({
        title: "Failed to publish workflow",
        description: getErrorMessage(error, "Unable to update publish status."),
        variant: "destructive",
      });
    } finally {
      setIsPublishPending(false);
    }
  };

  const handleScheduleToggle = async (nextValue: boolean) => {
    if (!workflowId) {
      setIsScheduled(false);
      toast({
        title: "Save workflow first",
        description: "Scheduling requires a saved workflow ID.",
        variant: "destructive",
      });
      return;
    }

    setIsSchedulePending(true);
    try {
      if (nextValue) {
        const result = await scheduleWorkflowFromLatestVersion(workflowId);
        if (result.status === "noop") {
          setIsScheduled(false);
          toast({
            title: "No schedule applied",
            description: result.message,
          });
          return;
        }

        setIsScheduled(true);
        toast({
          title: "Workflow scheduled",
          description: result.message,
        });
      } else {
        const result = await unscheduleWorkflow(workflowId);
        setIsScheduled(false);
        toast({
          title: "Workflow unscheduled",
          description: result.message,
        });
      }
    } catch (error) {
      setIsScheduled(!nextValue);
      toast({
        title: nextValue
          ? "Failed to schedule workflow"
          : "Failed to unschedule workflow",
        description: getErrorMessage(
          error,
          "Unable to update schedule status.",
        ),
        variant: "destructive",
      });
    } finally {
      setIsSchedulePending(false);
    }
  };

  const handleDeleteCurrentWorkflow = async () => {
    if (!workflowId) {
      return;
    }

    setIsDeletePending(true);
    try {
      await deleteWorkflow(workflowId);
      toast({
        title: "Colleague offboarded",
        description: `"${workflowName}" has been removed from your workspace.`,
      });
      setIsDeleteDialogOpen(false);
      navigate("/");
    } catch (error) {
      toast({
        title: "Failed to offboard colleague",
        description: getErrorMessage(error, "Unable to offboard colleague."),
        variant: "destructive",
      });
    } finally {
      setIsDeletePending(false);
    }
  };

  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
        <div className="flex items-center justify-between border-b pb-3">
          <div>
            <h2 className="text-lg font-semibold">
              {workflowName || "Workflow"}
            </h2>
            <p className="text-sm text-muted-foreground">
              {workflowName}
              {latestVersion ? ` · ${latestVersion.version}` : ""}
            </p>
          </div>
          <div className="flex items-center gap-4">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">
                      Publish
                    </span>
                    <Switch
                      aria-label="Publish workflow"
                      checked={isPublished}
                      onCheckedChange={(checked) =>
                        void handlePublishToggle(checked)
                      }
                      disabled={isPublishPending}
                    />
                  </div>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">
                  Expose this workflow through a shareable chat URL. When
                  publishing, choose <strong>Public</strong> (anyone with the
                  link) or <strong>Workspace only</strong> (sign-in required for
                  members of this workspace).
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Schedule</span>
              <Switch
                aria-label="Schedule workflow"
                checked={isScheduled}
                onCheckedChange={(checked) =>
                  void handleScheduleToggle(checked)
                }
                disabled={isSchedulePending || !canToggleSchedule}
              />
            </div>
            {workflowId && uploadsAllowed === true ? (
              <Button
                variant="outline"
                onClick={() => setIsUpdateDialogOpen(true)}
              >
                <RefreshCw className="mr-1.5 h-4 w-4" />
                Update
              </Button>
            ) : null}
            {canDeleteWorkflow ? (
              <Button
                variant="destructive"
                onClick={() => setIsDeleteDialogOpen(true)}
                disabled={isDeletePending}
              >
                <UserMinus className="mr-1.5 h-4 w-4" />
                Offboard
              </Button>
            ) : null}
            <Button
              onClick={() => {
                if (isRunPending || isRunning) {
                  return;
                }
                if (hasMissingCredentials) {
                  setIsMissingCredentialsDialogOpen(true);
                  return;
                }
                void onRunWorkflow();
              }}
              disabled={runButtonDisabled}
            >
              {isRunPending || isRunning ? (
                <LoaderCircle className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <Play className="mr-1.5 h-4 w-4" />
              )}
              {runButtonLabel}
            </Button>
            <Button
              variant="outline"
              onClick={() => setIsConfigOpen(true)}
              disabled={!canConfigure}
            >
              Config
            </Button>
          </div>
        </div>

        {isRunActive && (
          <div
            role="status"
            className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm text-primary"
          >
            Workflow run in progress. Check the latest record on the Trace tab
            for live status.
          </div>
        )}

        {runResultBanner && (
          <div
            role="status"
            className={`flex items-center gap-2 rounded-md border px-3 py-2 text-sm ${runResultBanner.className}`}
          >
            <runResultBanner.Icon className="h-4 w-4 shrink-0" />
            <span>{runResultBanner.message}</span>
          </div>
        )}

        {isPublished && shareUrl && (
          <div className="flex items-center justify-between rounded-md border border-border/60 bg-muted/20 px-3 py-2">
            <div className="min-w-0">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                {requireLogin ? "Members-only URL" : "Public URL"}
              </p>
              <a
                href={shareUrl}
                target="_blank"
                rel="noreferrer"
                className="block truncate text-sm text-primary hover:underline"
              >
                {shareUrl}
              </a>
            </div>
            <div className="ml-3 flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void handleCopyShareUrl()}
              >
                <Copy className="mr-1.5 h-3.5 w-3.5" />
                Copy
              </Button>
              <Button variant="outline" size="sm" asChild>
                <a href={shareUrl} target="_blank" rel="noreferrer">
                  <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                  Open
                </a>
              </Button>
            </div>
          </div>
        )}

        {hasMissingCredentials && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Missing credentials</AlertTitle>
            <AlertDescription>
              This workflow references credentials that are not in the vault.
              Add them before running:
              <ul className="mt-1 list-disc pl-5">
                {missingCredentials.map((name) => (
                  <li key={name} className="font-mono">
                    {name}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        {!latestVersion && (
          <div className="flex h-full items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground">
            No version is available yet to generate a Mermaid diagram.
          </div>
        )}

        {latestVersion && !mermaidSource && (
          <div className="flex h-full items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground">
            Mermaid data is unavailable for this workflow version.
          </div>
        )}

        {latestVersion && mermaidSource && diagramError && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
            Unable to render Mermaid diagram: {diagramError}
          </div>
        )}

        {latestVersion && mermaidSource && !diagramError && (
          <div className="min-h-0 flex flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-hidden">
              {diagramNodes.length > 0 ? (
                <ReactFlow
                  key={`${latestVersion.id}-mermaid-svg`}
                  nodes={diagramNodes}
                  edges={[]}
                  nodeTypes={nodeTypes}
                  fitView
                  minZoom={0.2}
                  maxZoom={2}
                  nodesDraggable={false}
                  nodesConnectable={false}
                  elementsSelectable={false}
                  zoomOnDoubleClick={false}
                  className="h-full w-full"
                  proOptions={{ hideAttribution: true }}
                  style={{ background: "transparent" }}
                >
                  <Controls showInteractive={false} />
                </ReactFlow>
              ) : (
                <pre className="h-full overflow-auto p-3 text-xs text-muted-foreground">
                  {defaultMermaid}
                </pre>
              )}
            </div>
          </div>
        )}

        <WorkflowConfigSheet
          open={isConfigOpen}
          onOpenChange={setIsConfigOpen}
          initialConfig={latestConfig}
          configurableSchemas={latestVersion?.configurableSchemas}
          onSave={onSaveConfig}
        />
      </div>

      {canDeleteWorkflow ? (
        <ConfirmDeleteWorkflowDialog
          open={isDeleteDialogOpen}
          workflowName={workflowName}
          isPending={isDeletePending}
          onOpenChange={setIsDeleteDialogOpen}
          onConfirm={handleDeleteCurrentWorkflow}
        />
      ) : null}

      {workflowId ? (
        <UpdateWorkflowDialog
          open={isUpdateDialogOpen}
          onOpenChange={setIsUpdateDialogOpen}
          workflowId={workflowId}
          workflowName={workflowName}
        />
      ) : null}

      <PublishWorkflowDialog
        open={isPublishDialogOpen}
        isPending={isPublishPending}
        onOpenChange={setIsPublishDialogOpen}
        onConfirm={handleConfirmPublish}
      />

      <AlertDialog
        open={isMissingCredentialsDialogOpen}
        onOpenChange={setIsMissingCredentialsDialogOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Missing credentials</AlertDialogTitle>
            <AlertDialogDescription>
              This workflow references credentials that are not in the vault.
              Running now will fail when those nodes execute. Add the following
              credentials before retrying:
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ul className="list-disc pl-6 text-sm">
            {missingCredentials.map((name) => (
              <li key={name} className="font-mono">
                {name}
              </li>
            ))}
          </ul>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setIsMissingCredentialsDialogOpen(false);
                void onRunWorkflow();
              }}
            >
              Run anyway
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
