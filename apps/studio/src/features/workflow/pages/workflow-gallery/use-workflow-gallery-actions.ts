import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "@/hooks/use-toast";
import {
  getWorkflowTemplateDefinition,
  type Workflow,
} from "@features/workflow/data/workflow-data";
import { getCandidateBadgeDefinition } from "@features/workflow/data/templates/candidate-badges";
import {
  deleteWorkflow,
  onboardCandidateAsWorkflow,
  updateCandidateWorkflowAsLatest,
} from "@features/workflow/lib/workflow-storage";
import {
  type ApiTeam,
  createTeam,
  deleteTeam,
  fetchWorkflowVersions,
} from "@features/workflow/lib/workflow-storage-api";
import { getWorkflowRouteRef } from "@features/workflow/lib/workflow-storage-helpers";
import { getSelectedWorkspaceSlug } from "@/lib/workspace-session";
import {
  getWorkspaceTeamWorkflowPath,
  getWorkspaceWorkflowPath,
} from "@/lib/workspace-routing";
import { type WorkflowGalleryTab } from "./types";

interface WorkflowGalleryActionsArgs {
  setSelectedTab: (value: WorkflowGalleryTab) => void;
  teams: ApiTeam[];
  refreshTeams: () => Promise<void>;
}

interface OnboardTarget {
  candidateId: string;
  candidateName: string;
}

const STARTER_TEMPLATE_IDS = ["template-python-agent"];
const WORKFLOW_FALLBACK_EXPORT_NAME = "workflow";

const toDownloadBasename = (workflowName: string): string => {
  const normalized = workflowName.trim().replace(/\s+/g, "-").toLowerCase();
  return normalized.length > 0 ? normalized : WORKFLOW_FALLBACK_EXPORT_NAME;
};

const getLangGraphSource = (
  workflowName: string,
  graph: Record<string, unknown>,
): string => {
  const graphFormat =
    typeof graph.format === "string" ? graph.format : "unknown";
  const graphSource = graph.source;

  if (
    graphFormat === "langgraph-script" &&
    typeof graphSource === "string" &&
    graphSource.trim().length > 0
  ) {
    return graphSource;
  }

  throw new Error(
    `Workflow '${workflowName}' uses unsupported format '${graphFormat}'. Only LangGraph script versions can be exported.`,
  );
};

export const resolveWorkflowPythonSource = async (
  workflow: Workflow,
): Promise<string> => {
  const templateDefinition = getWorkflowTemplateDefinition(workflow.id);
  if (
    templateDefinition &&
    typeof templateDefinition.script === "string" &&
    templateDefinition.script.trim().length > 0
  ) {
    return templateDefinition.script;
  }

  const versions = await fetchWorkflowVersions(workflow.id);
  if (versions.length === 0) {
    throw new Error(`Workflow '${workflow.name}' has no versions to export.`);
  }

  const latestVersion = versions.reduce((latest, current) =>
    current.version > latest.version ? current : latest,
  );

  if (!latestVersion.graph || typeof latestVersion.graph !== "object") {
    throw new Error(`Workflow '${workflow.name}' has no exportable source.`);
  }

  return getLangGraphSource(workflow.name, latestVersion.graph);
};

export const useWorkflowGalleryActions = (
  state: WorkflowGalleryActionsArgs,
) => {
  const navigate = useNavigate();
  const [onboardTarget, setOnboardTarget] = useState<OnboardTarget | null>(
    null,
  );
  const [isCreateTeamOpen, setIsCreateTeamOpen] = useState(false);

  const handleOpenWorkflow = useCallback(
    (workflowId: string, teamSlug?: string) => {
      const slug = getSelectedWorkspaceSlug();
      const path =
        teamSlug
          ? getWorkspaceTeamWorkflowPath(slug, teamSlug, workflowId)
          : getWorkspaceWorkflowPath(slug, workflowId);
      navigate(path);
    },
    [navigate],
  );

  const performOnboard = useCallback(
    async (candidateId: string, teamId?: string) => {
      try {
        const workflow = await onboardCandidateAsWorkflow(candidateId, teamId);

        state.setSelectedTab("all");

        toast({
          title: "Candidate onboarded",
          description: `"${workflow.name}" has been onboarded to your workspace.`,
        });

        const team = teamId ? state.teams.find((t) => t.id === teamId) : null;
        handleOpenWorkflow(getWorkflowRouteRef(workflow), team?.slug);
      } catch (error) {
        toast({
          title: "Failed to onboard candidate",
          description:
            error instanceof Error ? error.message : "Unknown error occurred",
          variant: "destructive",
        });
      }
    },
    [handleOpenWorkflow, state],
  );

  const handleUseTemplate = useCallback(
    async (templateId: string) => {
      const badge = getCandidateBadgeDefinition(templateId);
      if (!badge?.candidateId) {
        toast({
          title: "Candidate unavailable",
          description: "We couldn't find that candidate. Please try another.",
          variant: "destructive",
        });
        return;
      }

      // With more than one team, ask which team to onboard into. Otherwise the
      // server places the colleague in the workspace's default team.
      if (state.teams.length > 1) {
        setOnboardTarget({
          candidateId: badge.candidateId,
          candidateName: badge.name ?? badge.candidateId,
        });
        return;
      }

      await performOnboard(badge.candidateId);
    },
    [performOnboard, state.teams],
  );

  const confirmOnboardTeam = useCallback(
    async (teamId: string) => {
      const target = onboardTarget;
      setOnboardTarget(null);
      if (target) {
        await performOnboard(target.candidateId, teamId);
      }
    },
    [onboardTarget, performOnboard],
  );

  const cancelOnboardTeam = useCallback(() => {
    setOnboardTarget(null);
  }, []);

  const handleImportStarterPack = useCallback(async () => {
    try {
      const results = await Promise.allSettled(
        STARTER_TEMPLATE_IDS.flatMap((templateId) => {
          const badge = getCandidateBadgeDefinition(templateId);
          if (!badge?.candidateId) return [];
          return [onboardCandidateAsWorkflow(badge.candidateId)];
        }),
      );
      const importedCount = results.filter(
        (result) => result.status === "fulfilled" && result.value,
      ).length;

      if (importedCount === 0) {
        toast({
          title: "Starter pack unavailable",
          description:
            "No starter colleagues were imported. Please try again later.",
          variant: "destructive",
        });
        return;
      }

      state.setSelectedTab("all");

      toast({
        title: "Starter pack imported",
        description: `${importedCount} Python colleague${importedCount === 1 ? "" : "s"} added to your workspace.`,
      });
    } catch (error) {
      toast({
        title: "Failed to import starter pack",
        description:
          error instanceof Error ? error.message : "Unknown error occurred",
        variant: "destructive",
      });
    }
  }, [state]);

  const handleExportWorkflow = useCallback(async (workflow: Workflow) => {
    try {
      const source = await resolveWorkflowPythonSource(workflow);
      const fileBaseName = toDownloadBasename(workflow.name);
      const blob = new Blob([source], { type: "text/x-python" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${fileBaseName}.py`;
      anchor.click();
      URL.revokeObjectURL(url);

      toast({
        title: "Colleague transferred",
        description: `Downloaded ${fileBaseName}.py`,
      });
    } catch (error) {
      toast({
        title: "Transfer failed",
        description:
          error instanceof Error
            ? error.message
            : "Unable to transfer colleague.",
        variant: "destructive",
      });
    }
  }, []);

  const handleCreateTeam = useCallback(
    async (name: string, slug: string) => {
      await createTeam({ name, slug });
      await state.refreshTeams();
      toast({
        title: "Team created",
        description: `"${name}" is ready to group colleagues.`,
      });
    },
    [state],
  );

  const handleDeleteTeam = useCallback(
    async (teamId: string) => {
      const team = state.teams.find((t) => t.id === teamId);
      try {
        await deleteTeam(teamId);
        await state.refreshTeams();
        toast({
          title: "Team removed",
          description: team ? `"${team.name}" has been removed.` : undefined,
        });
      } catch (error) {
        toast({
          title: "Cannot remove team",
          description:
            error instanceof Error ? error.message : "Unknown error occurred",
          variant: "destructive",
        });
      }
    },
    [state],
  );

  const handleDeleteWorkflow = useCallback(
    async (workflowId: string, workflowName: string) => {
      try {
        await deleteWorkflow(workflowId);
        toast({
          title: "Colleague offboarded",
          description: `"${workflowName}" has been removed from your workspace.`,
        });
      } catch (error) {
        toast({
          title: "Failed to offboard colleague",
          description:
            error instanceof Error ? error.message : "Unknown error occurred",
          variant: "destructive",
        });
      }
    },
    [],
  );

  const handleUpdateCandidateWorkflow = useCallback(
    async (workflowId: string, candidateId: string) => {
      try {
        const workflow = await updateCandidateWorkflowAsLatest(
          workflowId,
          candidateId,
        );
        toast({
          title: "Candidate updated",
          description: `"${workflow.name}" is now on the latest candidate version.`,
        });
      } catch (error) {
        toast({
          title: "Failed to update candidate",
          description:
            error instanceof Error ? error.message : "Unknown error occurred",
          variant: "destructive",
        });
        throw error;
      }
    },
    [],
  );

  const openCreateTeamDialog = useCallback(() => setIsCreateTeamOpen(true), []);
  const closeCreateTeamDialog = useCallback(
    () => setIsCreateTeamOpen(false),
    [],
  );

  return {
    handleOpenWorkflow,
    handleUseTemplate,
    handleImportStarterPack,
    handleExportWorkflow,
    handleDeleteWorkflow,
    handleUpdateCandidateWorkflow,
    onboardTarget,
    confirmOnboardTeam,
    cancelOnboardTeam,
    isCreateTeamOpen,
    openCreateTeamDialog,
    closeCreateTeamDialog,
    handleCreateTeam,
    handleDeleteTeam,
  };
};
