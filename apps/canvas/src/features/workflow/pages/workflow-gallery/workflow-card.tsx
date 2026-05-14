import { useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent, SyntheticEvent } from "react";
import {
  Download,
  MoreHorizontal,
  Star,
  UserPlus,
  Trash,
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/design-system/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { ConfirmDeleteWorkflowDialog } from "@features/workflow/components/dialogs/confirm-delete-workflow-dialog";
import { getCandidateBadgeDefinition } from "@features/workflow/data/templates/candidate-badges";
import { type Workflow } from "@features/workflow/data/workflow-data";
import { getWorkflowRouteRef } from "@features/workflow/lib/workflow-storage-helpers";
import { VIBE_WORKFLOW_HANDLE } from "@features/vibe/constants";
import { WORKFLOW_GALLERY_CARD_ASPECT_CLASSNAME } from "./workflow-card-size";

const COLLEAGUE_EMOJIS = [
  // Office & business
  "👩‍💼", "👨‍💼", "🧑‍💼",
  // Tech
  "👩‍💻", "👨‍💻", "🧑‍💻",
  // Science & research
  "👩‍🔬", "👨‍🔬", "🧑‍🔬",
  // Creative & design
  "👩‍🎨", "👨‍🎨", "🧑‍🎨",
  // Education
  "👩‍🏫", "👨‍🏫", "🧑‍🏫",
  // Trades & engineering
  "👩‍🔧", "👨‍🔧", "🧑‍🔧",
  // Manufacturing
  "👩‍🏭", "👨‍🏭", "🧑‍🏭",
  // Healthcare
  "👩‍⚕️", "👨‍⚕️", "🧑‍⚕️",
  // Law & justice
  "👩‍⚖️", "👨‍⚖️", "🧑‍⚖️",
  // Culinary
  "👩‍🍳", "👨‍🍳", "🧑‍🍳",
  // Agriculture & nature
  "👩‍🌾", "👨‍🌾", "🧑‍🌾",
  // Space & exploration
  "👩‍🚀", "👨‍🚀", "🧑‍🚀",
  // Emergency & rescue
  "👩‍🚒", "👨‍🚒", "🧑‍🚒",
  // Security & military
  "👮‍♀️", "👮‍♂️", "👮",
  "💂‍♀️", "💂‍♂️", "💂",
  // Music & performance
  "👩‍🎤", "👨‍🎤", "🧑‍🎤",
  // Aviation
  "👩‍✈️", "👨‍✈️", "🧑‍✈️",
  // Sports & fitness
  "⛹️‍♀️", "⛹️‍♂️", "⛹️",
  "🏋️‍♀️", "🏋️‍♂️", "🏋️",
];

const getSeededIndex = (seed: string, length: number) => {
  let state = 2166136261;

  for (let index = 0; index < seed.length; index += 1) {
    state ^= seed.charCodeAt(index);
    state = Math.imul(state, 16777619);
  }

  return Math.abs(state) % length;
};

const getWorkflowTemplateEmoji = (workflow: Workflow) => {
  const templateId = workflow.versions?.at(-1)?.templateId;
  if (!templateId) {
    return undefined;
  }

  return getCandidateBadgeDefinition(templateId)?.emoji;
};

interface WorkflowCardProps {
  workflow: Workflow;
  isTemplate: boolean;
  workspaceLabel: string;
  onOpenWorkflow: (workflowId: string) => void;
  onUseTemplate: (workflowId: string) => void;
  onExportWorkflow: (workflow: Workflow) => void;
  onDeleteWorkflow: (
    workflowId: string,
    workflowName: string,
  ) => Promise<void> | void;
}

export const WorkflowCard = ({
  workflow,
  isTemplate,
  workspaceLabel,
  onOpenWorkflow,
  onUseTemplate,
  onExportWorkflow,
  onDeleteWorkflow,
}: WorkflowCardProps) => {
  const workflowRouteRef = getWorkflowRouteRef(workflow);
  const isClickable = !isTemplate;
  const canDeleteWorkflow =
    !isTemplate && workflow.handle !== VIBE_WORKFLOW_HANDLE;
  const candidateBadge = isTemplate
    ? getCandidateBadgeDefinition(workflow.id)
    : undefined;
  const headerLabel = isTemplate ? "Candidate" : workspaceLabel;
  const workflowSlug = workflow.handle ?? workflow.id;
  const workflowAvatarEmoji = getWorkflowTemplateEmoji(workflow);

  const suppressCardOpenRef = useRef(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isDeletePending, setIsDeletePending] = useState(false);

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

  return (
    <>
      <Card
        className={cn(
          "flex w-full flex-col overflow-hidden border-border/60 bg-[#f5f5f1] text-[#141412] shadow-[0_16px_48px_rgba(0,0,0,0.14)] transition-transform duration-200 hover:-translate-y-1",
          WORKFLOW_GALLERY_CARD_ASPECT_CLASSNAME,
          isClickable && "cursor-pointer",
        )}
        data-testid="workflow-card"
        onClick={handleCardOpen}
        onKeyDown={handleCardKeyDown}
        role={isClickable ? "button" : undefined}
        tabIndex={isClickable ? 0 : undefined}
      >
        <CardHeader className="relative flex h-12 items-center justify-center overflow-hidden border-b border-black/5 bg-[#1a1a1a] px-4 py-0 text-center">
          <div
            className="absolute inset-0 opacity-30"
            aria-hidden="true"
            style={{
              backgroundImage:
                "radial-gradient(rgba(255, 255, 255, 0.14) 1px, transparent 1px)",
              backgroundSize: "30px 18px",
            }}
          />

          <div className="relative z-10 flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-white/60">
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
                className="absolute right-2 top-2 z-10 h-7 w-7 text-white/70 hover:bg-white/10 hover:text-white"
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
                  <Download className="mr-2 h-4 w-4" />
                  Export
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
                    <Download className="mr-2 h-4 w-4" />
                    Export
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
                        <Trash className="mr-2 h-4 w-4" />
                        Delete
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
            <div className="absolute left-0 top-0 flex items-center gap-1 rounded-md border border-[#1a1a1a]/15 bg-[#1a1a1a]/5 px-2 py-1">
              <span className="h-1.5 w-1.5 rounded-full bg-[#1a1a1a]/70 shadow-[0_0_6px_rgba(26,26,26,0.4)]" />
              <span className="font-mono text-[9px] font-bold uppercase tracking-[0.1em] text-[#1a1a1a]/80">
                AI
              </span>
            </div>

            <div className="flex h-24 w-24 items-center justify-center overflow-hidden rounded-full border-4 border-[#1a1a1a] bg-[#e4e4e0] shadow-[0_4px_16px_rgba(0,0,0,0.22)]">
              <span
                aria-hidden="true"
                className="select-none text-[3.5rem] leading-none"
              >
                {workflowAvatarEmoji ??
                  candidateBadge?.emoji ??
                  COLLEAGUE_EMOJIS[
                    getSeededIndex(workflowSlug, COLLEAGUE_EMOJIS.length)
                  ]}
              </span>
            </div>

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

          <div className="mt-4 shrink-0 text-[1.35rem] font-semibold tracking-[-0.025em] text-[#141412]">
            {workflow.name}
          </div>

          <div className="mt-1 shrink-0 font-mono text-[10px] tracking-[0.04em] text-[#aaa]">
            @{workflowSlug}
          </div>

          <div className="mt-3 flex min-h-0 w-full flex-1 flex-col items-center justify-start">
            <div className="h-px w-8 rounded-full bg-[#1a1a1a]/20" />

            {candidateBadge ? (
              <div className="mt-1 shrink-0 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[#888]">
                {candidateBadge.subtitle}
              </div>
            ) : null}

            <p className="mt-1 w-full flex-1 overflow-hidden text-[13px] leading-7 text-[#3a3a38]">
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
    </>
  );
};
