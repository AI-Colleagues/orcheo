import { useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent, SyntheticEvent } from "react";
import { toast } from "@/hooks/use-toast";
import { Button } from "@/design-system/ui/button";
import { Badge } from "@/design-system/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { Avatar, AvatarFallback, AvatarImage } from "@/design-system/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/design-system/ui/dropdown-menu";
import {
  AlertCircle,
  CheckCircle,
  Clock,
  Copy,
  Download,
  FolderPlus,
  MoreHorizontal,
  Pencil,
  Star,
  Trash,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ConfirmDeleteWorkflowDialog } from "@features/workflow/components/dialogs/confirm-delete-workflow-dialog";
import { type Workflow } from "@features/workflow/data/workflow-data";
import { getWorkflowRouteRef } from "@features/workflow/lib/workflow-storage-helpers";
import { VIBE_WORKFLOW_HANDLE } from "@features/vibe/constants";
import { WorkflowThumbnail } from "./workflow-thumbnail";

interface WorkflowCardProps {
  workflow: Workflow;
  isTemplate: boolean;
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
  onOpenWorkflow,
  onUseTemplate,
  onExportWorkflow,
  onDeleteWorkflow,
}: WorkflowCardProps) => {
  const updatedLabel = new Date(
    workflow.updatedAt || workflow.createdAt,
  ).toLocaleDateString();
  const workflowRouteRef = getWorkflowRouteRef(workflow);
  const isClickable = !isTemplate;
  const canDeleteWorkflow =
    !isTemplate && workflow.handle !== VIBE_WORKFLOW_HANDLE;
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
    if (isClickable) {
      if (isMenuOpen) {
        return;
      }
      if (suppressCardOpenRef.current) {
        return;
      }
      const target = event.target as HTMLElement;
      if (target.closest('[data-card-action="true"]')) {
        return;
      }
      onOpenWorkflow(workflowRouteRef);
    }
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
          "overflow-hidden",
          isClickable && "cursor-pointer transition-colors hover:bg-muted/20",
        )}
        data-testid="workflow-card"
        onClick={handleCardOpen}
        onKeyDown={handleCardKeyDown}
        role={isClickable ? "button" : undefined}
        tabIndex={isClickable ? 0 : undefined}
      >
        <CardHeader className="px-3 pb-2 pt-3">
          <div className="flex items-start justify-between">
            <CardTitle className="text-base">{workflow.name}</CardTitle>
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
                  className="h-7 w-7"
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
                  <>
                    <DropdownMenuItem
                      onSelect={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        onUseTemplate(workflow.id);
                      }}
                    >
                      <Copy className="mr-2 h-4 w-4" />
                      Use template
                    </DropdownMenuItem>
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
                  </>
                ) : (
                  <>
                    <DropdownMenuItem
                      onSelect={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        onOpenWorkflow(workflowRouteRef);
                      }}
                    >
                      <Pencil className="mr-2 h-4 w-4" />
                      Edit
                    </DropdownMenuItem>
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
          </div>

          <CardDescription className="line-clamp-1">
            {workflow.description || "No description provided"}
          </CardDescription>
          {!isTemplate && (
            <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
              {workflow.handle && (
                <code className="rounded bg-muted px-1.5 py-0.5">
                  handle: {workflow.handle}
                </code>
              )}
              <code className="rounded bg-muted px-1.5 py-0.5">
                id: {workflow.id}
              </code>
            </div>
          )}

          {isTemplate && workflow.sourceExample && (
            <p className="mt-1 line-clamp-1 text-xs text-muted-foreground/80">
              Based on {workflow.sourceExample}
            </p>
          )}
        </CardHeader>

        <CardContent className="px-3 pb-2">
          <WorkflowThumbnail workflow={workflow} />
          <div className="mt-2 flex flex-wrap gap-1">
            {workflow.tags.slice(0, 2).map((tag) => (
              <Badge key={tag} variant="secondary" className="text-xs">
                {tag}
              </Badge>
            ))}
            {workflow.tags.length > 2 && (
              <Badge variant="secondary" className="text-xs">
                +{workflow.tags.length - 2} more
              </Badge>
            )}
          </div>
        </CardContent>

        <CardFooter className="flex justify-between px-3 pb-3 pt-2 text-xs text-muted-foreground">
          <div className="flex items-center">
            <Avatar className="mr-1 h-5 w-5">
              <AvatarImage src={workflow.owner.avatar} />
              <AvatarFallback>{workflow.owner.name.charAt(0)}</AvatarFallback>
            </Avatar>
            <div className="flex items-center gap-1">
              <span>{updatedLabel}</span>
              {workflow.lastRun?.status === "success" && (
                <CheckCircle className="h-3 w-3 text-green-500" />
              )}
              {workflow.lastRun?.status === "error" && (
                <AlertCircle className="h-3 w-3 text-red-500" />
              )}
              {workflow.lastRun?.status === "running" && (
                <Clock className="h-3 w-3 animate-pulse text-blue-500" />
              )}
            </div>
          </div>

          <div className="flex gap-1">
            {isTemplate ? (
              <Button
                size="sm"
                className="h-7 px-3 text-xs"
                data-card-action="true"
                onClick={(event) => {
                  stopPropagation(event);
                  onUseTemplate(workflow.id);
                }}
                onPointerDown={stopPropagation}
              >
                <FolderPlus className="mr-1 h-3 w-3" />
                Use template
              </Button>
            ) : (
              <>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  aria-label="Favorite workflow"
                  data-card-action="true"
                  onClick={(event) => {
                    stopPropagation(event);
                    toast({
                      title: "Favorites coming soon",
                      description: `We'll remember ${workflow.name} as a favorite soon.`,
                    });
                  }}
                  onPointerDown={stopPropagation}
                >
                  <Star className="h-3 w-3" />
                </Button>
                <Button
                  size="sm"
                  className="h-7 px-2 text-xs"
                  data-card-action="true"
                  onClick={(event) => {
                    stopPropagation(event);
                    onOpenWorkflow(workflowRouteRef);
                  }}
                  onPointerDown={stopPropagation}
                >
                  <Pencil className="mr-1 h-3 w-3" />
                  Edit
                </Button>
              </>
            )}
          </div>
        </CardFooter>
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
