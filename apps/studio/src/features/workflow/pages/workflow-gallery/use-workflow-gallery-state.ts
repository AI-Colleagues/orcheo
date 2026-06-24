import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "@/hooks/use-toast";
import {
  GALLERY_TEMPLATE_WORKFLOWS,
  type Workflow,
} from "@features/workflow/data/workflow-data";
import {
  type CandidateBadgeSpec,
  getCandidateWorkflows,
  setCandidateBadges,
} from "@features/workflow/data/templates/candidate-badges";
import {
  listWorkflows,
  type StoredWorkflow,
  WORKFLOW_STORAGE_EVENT,
} from "@features/workflow/lib/workflow-storage";
import {
  type ApiCandidate,
  type ApiTeam,
  fetchCandidates,
  fetchTeams,
} from "@features/workflow/lib/workflow-storage-api";
import {
  type WorkflowGalleryTab,
  type WorkflowGalleryTabCounts,
} from "./types";

interface WorkflowGalleryStateSlice {
  searchQuery: string;
  setSearchQuery: (value: string) => void;
  selectedTab: WorkflowGalleryTab;
  setSelectedTab: (value: WorkflowGalleryTab) => void;
  isLoadingWorkflows: boolean;
  sortedWorkflows: Workflow[];
  tabCounts: WorkflowGalleryTabCounts;
  isTemplateView: boolean;
  templates: Workflow[];
  teams: ApiTeam[];
  refreshTeams: () => Promise<void>;
}

const toCandidateSpec = (candidate: ApiCandidate): CandidateBadgeSpec => ({
  id: `template-${candidate.id.replace(/\//g, "-")}`,
  candidateId: candidate.id,
  name: candidate.name,
  handle: candidate.handle,
  subtitle: candidate.subtitle ?? undefined,
  description: candidate.description ?? undefined,
  avatar: candidate.avatar ?? undefined,
  notes: candidate.notes,
  mermaid: candidate.mermaid,
  rawMetadata: candidate.metadata,
  version: candidate.version,
  updates: candidate.updates,
});

const matchesWorkflowSearch = (
  workflow: Workflow,
  normalizedSearchQuery: string,
) => {
  return (
    normalizedSearchQuery.length === 0 ||
    workflow.name.toLowerCase().includes(normalizedSearchQuery) ||
    (workflow.description?.toLowerCase().includes(normalizedSearchQuery) ??
      false)
  );
};

export const useWorkflowGalleryState = (): WorkflowGalleryStateSlice => {
  const [workflows, setWorkflows] = useState<StoredWorkflow[]>([]);
  const [candidateWorkflows, setCandidateWorkflows] = useState<Workflow[]>([]);
  const [teams, setTeams] = useState<ApiTeam[]>([]);
  const [isLoadingWorkflows, setIsLoadingWorkflows] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTab, setSelectedTab] = useState<WorkflowGalleryTab>("all");

  useEffect(() => {
    let isMounted = true;

    const loadCandidates = async () => {
      try {
        // Load both candidates and existing workflows to check for handle conflicts
        const [candidates, existingWorkflows] = await Promise.all([
          fetchCandidates(),
          listWorkflows(),
        ]);
        if (!isMounted) {
          return;
        }
        setCandidateBadges(candidates.map(toCandidateSpec), existingWorkflows);
        setCandidateWorkflows(getCandidateWorkflows());
      } catch (error) {
        if (isMounted) {
          console.error("Failed to load candidate colleagues", error);
        }
      }
    };

    void loadCandidates();

    return () => {
      isMounted = false;
    };
  }, []);

  const refreshTeams = useCallback(async () => {
    try {
      const teamList = await fetchTeams();
      setTeams(teamList);
    } catch {
      // non-fatal — teams list will stay stale
    }
  }, []);

  useEffect(() => {
    let isMounted = true;

    const load = async (forceRefresh = false) => {
      if (isMounted) {
        setIsLoadingWorkflows(true);
      }
      try {
        const [items, teamList] = await Promise.all([
          listWorkflows({ forceRefresh }),
          fetchTeams().catch(() => [] as ApiTeam[]),
        ]);
        if (isMounted) {
          setWorkflows(items);
          setTeams(teamList);
        }
      } catch (error) {
        if (!isMounted) {
          return;
        }

        console.error("Failed to load colleagues", error);
        toast({
          title: "Unable to load colleagues",
          description:
            error instanceof Error ? error.message : "Unknown error occurred",
          variant: "destructive",
        });
      } finally {
        if (isMounted) {
          setIsLoadingWorkflows(false);
        }
      }
    };

    void load();

    const targetWindow = typeof window !== "undefined" ? window : undefined;
    if (targetWindow) {
      const handler = () => {
        void load(true);
      };
      targetWindow.addEventListener(WORKFLOW_STORAGE_EVENT, handler);

      return () => {
        isMounted = false;
        targetWindow.removeEventListener(WORKFLOW_STORAGE_EVENT, handler);
      };
    }

    return () => {
      isMounted = false;
    };
  }, []);

  const templates = useMemo(
    () => [...GALLERY_TEMPLATE_WORKFLOWS, ...candidateWorkflows],
    [candidateWorkflows],
  );
  const isTemplateView = selectedTab === "templates";
  const normalizedSearchQuery = searchQuery.trim().toLowerCase();

  const searchableWorkflows = useMemo(() => {
    return workflows.filter((workflow) =>
      matchesWorkflowSearch(workflow, normalizedSearchQuery),
    );
  }, [workflows, normalizedSearchQuery]);

  const searchableTemplates = useMemo(() => {
    return templates.filter(
      (workflow) =>
        matchesWorkflowSearch(workflow, normalizedSearchQuery) &&
        workflow.tags.includes("template"),
    );
  }, [templates, normalizedSearchQuery]);

  const tabCounts = useMemo<WorkflowGalleryTabCounts>(() => {
    return {
      all: searchableWorkflows.length,
      pinned: searchableWorkflows.filter((workflow) =>
        workflow.tags.includes("favorite"),
      ).length,
      templates: searchableTemplates.length,
    };
  }, [searchableTemplates, searchableWorkflows]);

  const filteredWorkflows = useMemo(() => {
    if (isTemplateView) {
      return searchableTemplates;
    }

    if (selectedTab === "pinned") {
      return searchableWorkflows.filter((workflow) =>
        workflow.tags.includes("favorite"),
      );
    }

    return searchableWorkflows;
  }, [isTemplateView, searchableTemplates, searchableWorkflows, selectedTab]);

  return {
    searchQuery,
    setSearchQuery,
    selectedTab,
    setSelectedTab,
    isLoadingWorkflows,
    sortedWorkflows: filteredWorkflows,
    tabCounts,
    isTemplateView,
    templates,
    teams,
    refreshTeams,
  };
};
