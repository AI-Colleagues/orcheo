import { useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent, SyntheticEvent } from "react";
import {
  Loader2,
  MoreHorizontal,
  RefreshCcw,
  Send,
  Star,
  UserMinus,
  UserPlus,
} from "lucide-react";
import { toast } from "@/hooks/use-toast";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from "@/design-system/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/design-system/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/design-system/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/design-system/ui/tooltip";
import { cn } from "@/lib/utils";
import { resolveAvatarUrl } from "@/assets/avatars";
import { ConfirmDeleteWorkflowDialog } from "@features/workflow/components/dialogs/confirm-delete-workflow-dialog";
import {
  getCandidateBadgeByCandidateId,
  getCandidateBadgeByHandle,
  getCandidateBadgeDefinition,
} from "@features/workflow/data/templates/candidate-badges";
import { type Workflow } from "@features/workflow/data/workflow-data";
import { getWorkflowRouteRef } from "@features/workflow/lib/workflow-storage-helpers";
import { getCandidateUpdateAvailability } from "./candidate-updates";
import { WORKFLOW_GALLERY_CARD_ASPECT_CLASSNAME } from "./workflow-card-size";

const getWorkflowTemplateAvatar = (workflow: Workflow): string | undefined => {
  const templateId = workflow.versions?.at(-1)?.templateId;
  if (!templateId) {
    return undefined;
  }
  return (
    getCandidateBadgeDefinition(templateId)?.workflow.avatarEmoji ?? undefined
  );
};

interface WorkflowCardProps {
  workflow: Workflow;
  isTemplate: boolean;
  teamSlug?: string;
  workspaceLabel: string;
  onOpenWorkflow: (workflowId: string) => void;
  onUseTemplate: (workflowId: string) => void;
  onExportWorkflow: (workflow: Workflow) => void;
  onDeleteWorkflow: (
    workflowId: string,
    workflowName: string,
  ) => Promise<void> | void;
  onUpdateCandidateWorkflow: (
    workflowId: string,
    candidateId: string,
  ) => Promise<void> | void;
}

export const WorkflowCard = ({
  workflow,
  isTemplate,
  teamSlug,
  workspaceLabel,
  onOpenWorkflow,
  onUseTemplate,
  onExportWorkflow,
  onDeleteWorkflow,
  onUpdateCandidateWorkflow,
}: WorkflowCardProps) => {
  const workflowRouteRef = getWorkflowRouteRef(workflow);
  const isClickable = !isTemplate;
  const canDeleteWorkflow = !isTemplate;
  const candidateBadge = isTemplate
    ? getCandidateBadgeDefinition(workflow.id)
    : undefined;
  const templateId = !isTemplate
    ? workflow.versions?.at(-1)?.templateId
    : undefined;
  const candidateSource = !isTemplate
    ? workflow.versions?.at(-1)?.candidateSource
    : undefined;
  const templateBadge = !isTemplate
    ? ((candidateSource?.candidateId
        ? getCandidateBadgeByCandidateId(candidateSource.candidateId)
        : undefined) ??
      (candidateSource?.candidateHandle
        ? getCandidateBadgeByHandle(candidateSource.candidateHandle)
        : undefined) ??
      (templateId ? getCandidateBadgeDefinition(templateId) : undefined) ??
      (workflow.handle
        ? getCandidateBadgeByHandle(workflow.handle)
        : undefined))
    : undefined;
  const displayBadge = candidateBadge ?? templateBadge;
  const candidateUpdate = !isTemplate
    ? getCandidateUpdateAvailability(workflow, templateBadge)
    : undefined;
  const headerLabel = isTemplate ? "Candidate" : workspaceLabel;
  const rawHandle = workflow.handle ?? workflow.id;
  const workflowSlug = isTemplate
    ? rawHandle.replace(/^template-/, "").replace(/_/g, "-")
    : rawHandle;
  const displayHandle =
    !isTemplate && teamSlug ? `${teamSlug}/${workflowSlug}` : workflowSlug;
  const avatarUrl = resolveAvatarUrl(
    workflow.avatarEmoji ?? getWorkflowTemplateAvatar(workflow),
    workflow.id,
  );

  const suppressCardOpenRef = useRef(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isDeletePending, setIsDeletePending] = useState(false);
  const [isUpdateDialogOpen, setIsUpdateDialogOpen] = useState(false);
  const [isUpdatePending, setIsUpdatePending] = useState(false);

  const suppressCardOpen = () => {
    suppressCardOpenRef.current = true;
    setTimeout(() => {
      suppressCardOpenRef.current = false;
    }, 0);
  };

  const handleCardOpen = (event: MouseEvent<HTMLDivElement>) => {
    if (!isClickable) {
      return;
    }
    if (isMenuOpen || suppressCardOpenRef.current) {
      return;
    }

    const target = event.target as HTMLElement;
    if (target.closest('[data-card-action="true"]')) {
      return;
    }

    onOpenWorkflow(workflowRouteRef);
  };

  const handleCardKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!isClickable) {
      return;
    }

    const target = event.target as HTMLElement;
    if (target.closest('[data-card-action="true"]')) {
      return;
    }

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onOpenWorkflow(workflowRouteRef);
    }
  };

  const stopPropagation = (event: SyntheticEvent) => {
    event.stopPropagation();
    suppressCardOpen();
  };

  const handleConfirmDelete = async () => {
    setIsDeletePending(true);
    try {
      await onDeleteWorkflow(workflow.id, workflow.name);
      setIsDeleteDialogOpen(false);
    } finally {
      setIsDeletePending(false);
    }
  };

  const handleConfirmUpdate = async () => {
    if (!candidateUpdate) {
      return;
    }

    setIsUpdatePending(true);
    try {
      await onUpdateCandidateWorkflow(workflow.id, candidateUpdate.candidateId);
      setIsUpdateDialogOpen(false);
    } finally {
      setIsUpdatePending(false);
    }
  };

  return (
    <>
      <Card
        className={cn(
          "flex w-full flex-col overflow-hidden border-border/70 bg-card text-card-foreground shadow-[0_16px_48px_rgba(15,23,42,0.08)] transition-transform duration-200 hover:-translate-y-1 dark:shadow-[0_16px_48px_rgba(0,0,0,0.45)] text-[20px] md:text-[14px]",
          WORKFLOW_GALLERY_CARD_ASPECT_CLASSNAME,
          isClickable && "cursor-pointer",
        )}
        data-testid="workflow-card"
        onClick={handleCardOpen}
        onKeyDown={handleCardKeyDown}
        role={isClickable ? "button" : undefined}
        tabIndex={isClickable ? 0 : undefined}
      >
        <CardHeader className="relative flex h-12 items-center justify-center overflow-hidden border-b border-border/70 bg-gradient-to-r from-slate-950 via-slate-900 to-slate-800 px-4 py-0 text-center text-slate-50 dark:border-border/60 dark:from-slate-100 dark:via-slate-50 dark:to-slate-200 dark:text-slate-950">
          <div
            className="absolute inset-0 opacity-25 dark:opacity-40"
            aria-hidden="true"
            style={{
              backgroundImage:
                "radial-gradient(rgba(255, 255, 255, 0.16) 1px, transparent 1px)",
              backgroundSize: "30px 18px",
            }}
          />

          <div className="relative z-10 flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-white/70 dark:text-slate-700">
              {headerLabel}
            </span>
          </div>

          <DropdownMenu
            open={isMenuOpen}
            onOpenChange={(open) => {
              setIsMenuOpen(open);
              if (!open) {
                suppressCardOpen();
              }
            }}
          >
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-2 top-2 z-10 h-7 w-7 text-white/75 hover:bg-white/10 hover:text-white dark:text-slate-700 dark:hover:bg-slate-950/10 dark:hover:text-slate-950"
                onClick={stopPropagation}
                onPointerDown={stopPropagation}
                aria-label="Workflow actions"
                data-card-action="true"
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {isTemplate ? (
                <DropdownMenuItem
                  onSelect={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onExportWorkflow(workflow);
                  }}
                >
                  <Send className="mr-2 h-4 w-4" />
                  Transfer
                </DropdownMenuItem>
              ) : (
                <>
                  <DropdownMenuItem
                    onSelect={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      onExportWorkflow(workflow);
                    }}
                  >
                    <Send className="mr-2 h-4 w-4" />
                    Transfer
                  </DropdownMenuItem>
                  {canDeleteWorkflow ? (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        className="text-red-600"
                        onSelect={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          setIsDeleteDialogOpen(true);
                        }}
                      >
                        <UserMinus className="mr-2 h-4 w-4" />
                        Offboard
                      </DropdownMenuItem>
                    </>
                  ) : null}
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </CardHeader>

        <CardContent className="flex flex-1 min-h-0 flex-col items-center px-5 py-3 text-center">
          <div className="relative flex w-full shrink-0 items-start justify-center">
            <div className="absolute left-0 top-0 flex items-center gap-1 rounded-md border border-border/70 bg-muted/60 px-2 py-1 text-muted-foreground shadow-sm dark:bg-slate-950/20">
              <span className="h-1.5 w-1.5 rounded-full bg-slate-800 shadow-[0_0_6px_rgba(15,23,42,0.32)] dark:bg-slate-200 dark:shadow-[0_0_6px_rgba(226,232,240,0.28)]" />
              <span className="font-mono text-[9px] font-bold uppercase tracking-[0.1em] text-slate-700 dark:text-slate-300">
                AI
              </span>
            </div>

            <div className="flex h-[7em] w-[7em] items-center justify-center overflow-hidden rounded-full border-4 border-slate-900 bg-slate-100 shadow-[0_4px_16px_rgba(15,23,42,0.18)] dark:border-slate-100 dark:bg-slate-800 dark:shadow-[0_4px_16px_rgba(0,0,0,0.35)]">
              <img
                src={avatarUrl}
                alt={workflow.name}
                className="h-full w-full object-cover"
                aria-hidden="true"
              />
            </div>

            {candidateUpdate ? (
              <TooltipProvider delayDuration={200}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="outline"
                      size="icon"
                      className="absolute right-10 top-0 h-8 w-8 border-emerald-500/40 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-950/40 dark:text-emerald-300 dark:hover:bg-emerald-900/60"
                      aria-label={`Update ${workflow.name} to candidate version ${candidateUpdate.latestVersion}`}
                      data-card-action="true"
                      onClick={(event) => {
                        stopPropagation(event);
                        setIsUpdateDialogOpen(true);
                      }}
                      onPointerDown={stopPropagation}
                    >
                      <RefreshCcw className="h-3.5 w-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="max-w-[280px]">
                    <div className="space-y-2 text-xs">
                      <div className="font-semibold text-foreground">
                        Candidate version {candidateUpdate.currentVersion} to{" "}
                        {candidateUpdate.latestVersion}
                      </div>
                      {candidateUpdate.latestSummary ? (
                        <p>{candidateUpdate.latestSummary}</p>
                      ) : null}
                      {candidateUpdate.firstMigration ? (
                        <p className="text-amber-600 dark:text-amber-300">
                          {candidateUpdate.firstMigration}
                        </p>
                      ) : null}
                    </div>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : null}

            {!isTemplate ? (
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-0 top-0 h-8 w-8"
                aria-label="Star workflow"
                data-card-action="true"
                onClick={(event) => {
                  stopPropagation(event);
                  toast({
                    title: "Starred workflows coming soon",
                    description: `We'll remember ${workflow.name} as a starred workflow soon.`,
                  });
                }}
                onPointerDown={stopPropagation}
              >
                <Star className="h-3 w-3" />
              </Button>
            ) : null}
          </div>

          <div className="mt-4 shrink-0 text-[1.5em] font-semibold tracking-[-0.025em] text-card-foreground">
            {workflow.name}
          </div>

          <div className="mt-1 shrink-0 font-mono text-[10px] tracking-[0.04em] text-muted-foreground">
            @{displayHandle}
          </div>

          <div className="mt-3 flex min-h-0 w-full flex-1 flex-col items-center justify-start">
            <div className="h-px w-8 rounded-full bg-border/80" />

            {displayBadge?.subtitle ? (
              <div className="mt-1 shrink-0 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                {displayBadge.subtitle}
              </div>
            ) : null}

            <p className="mt-1 w-full flex-1 overflow-hidden text-[13px] leading-7 text-muted-foreground">
              {workflow.description || "No description provided"}
            </p>
          </div>
        </CardContent>

        {isTemplate ? (
          <CardFooter className="flex items-center justify-end gap-2 px-4 py-1.5">
            <Button
              size="sm"
              className="h-8 px-3 text-xs"
              data-card-action="true"
              onClick={(event) => {
                stopPropagation(event);
                toast({
                  title: "Onboarding agent…",
                  description:
                    "Setting up your new agent. This may take a few seconds.",
                });
                onUseTemplate(workflow.id);
              }}
              onPointerDown={stopPropagation}
            >
              <UserPlus className="mr-1 h-3 w-3" />
              Onboard
            </Button>
          </CardFooter>
        ) : null}
      </Card>

      {canDeleteWorkflow ? (
        <ConfirmDeleteWorkflowDialog
          open={isDeleteDialogOpen}
          workflowName={workflow.name}
          isPending={isDeletePending}
          onOpenChange={setIsDeleteDialogOpen}
          onConfirm={handleConfirmDelete}
        />
      ) : null}

      {candidateUpdate ? (
        <Dialog
          open={isUpdateDialogOpen}
          onOpenChange={(open) => {
            if (!isUpdatePending) {
              setIsUpdateDialogOpen(open);
            }
          }}
        >
          <DialogContent className="max-w-xl">
            <DialogHeader>
              <DialogTitle>Update candidate version</DialogTitle>
              <DialogDescription>
                Review release notes before updating {workflow.name} from{" "}
                {candidateUpdate.currentVersion} to{" "}
                {candidateUpdate.latestVersion}.
              </DialogDescription>
            </DialogHeader>

            {candidateUpdate.isMajor ? (
              <div className="rounded-md border border-amber-500/50 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                This is a major candidate update. Review migration notes before
                applying it to this colleague.
              </div>
            ) : null}

            <div className="max-h-[320px] space-y-3 overflow-y-auto pr-1">
              {candidateUpdate.crossedUpdates.length > 0 ? (
                candidateUpdate.crossedUpdates.map((note) => (
                  <div
                    key={note.version}
                    className="rounded-md border border-border/70 p-3 text-sm"
                  >
                    <div className="font-semibold">
                      Candidate version {note.version}
                    </div>
                    <p className="mt-1 text-muted-foreground">{note.summary}</p>
                    {note.migration ? (
                      <p className="mt-2 text-amber-700 dark:text-amber-300">
                        {note.migration}
                      </p>
                    ) : null}
                  </div>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">
                  No release notes were provided for the versions being crossed.
                </p>
              )}
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsUpdateDialogOpen(false)}
                disabled={isUpdatePending}
              >
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleConfirmUpdate}
                disabled={isUpdatePending}
              >
                {isUpdatePending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCcw className="mr-2 h-4 w-4" />
                )}
                Update
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
};
