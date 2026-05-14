import { useEffect, useMemo, useState } from "react";
import { toast } from "@/hooks/use-toast";
import {
  GALLERY_TEMPLATE_WORKFLOWS,
  type Workflow,
} from "@features/workflow/data/workflow-data";
import {
  listWorkflows,
  type StoredWorkflow,
  WORKFLOW_STORAGE_EVENT,
} from "@features/workflow/lib/workflow-storage";
import {
  type WorkflowGalleryFilters,
  type WorkflowGallerySort,
  type WorkflowGalleryTab,
  type WorkflowGalleryTabCounts,
} from "./types";

interface WorkflowGalleryStateSlice {
  searchQuery: string;
  setSearchQuery: (value: string) => void;
  selectedTab: WorkflowGalleryTab;
  setSelectedTab: (value: WorkflowGalleryTab) => void;
  sortBy: WorkflowGallerySort;
  setSortBy: (value: WorkflowGallerySort) => void;
  newFolderName: string;
  setNewFolderName: (value: string) => void;
  showNewFolderDialog: boolean;
  setShowNewFolderDialog: (value: boolean) => void;
  showFilterPopover: boolean;
  setShowFilterPopover: (value: boolean) => void;
  filters: WorkflowGalleryFilters;
  setFilters: (value: WorkflowGalleryFilters) => void;
  isLoadingWorkflows: boolean;
  sortedWorkflows: Workflow[];
  tabCounts: WorkflowGalleryTabCounts;
  isTemplateView: boolean;
  templates: Workflow[];
}

const DEFAULT_FILTERS: WorkflowGalleryFilters = {
  owner: {
    me: true,
    shared: true,
  },
  status: {
    active: true,
    draft: true,
    archived: false,
  },
  tags: {
    favorite: false,
    template: false,
    production: false,
    development: false,
  },
};

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
  const [isLoadingWorkflows, setIsLoadingWorkflows] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTab, setSelectedTab] = useState<WorkflowGalleryTab>("all");
  const [sortBy, setSortBy] = useState<WorkflowGallerySort>("updated");
  const [newFolderName, setNewFolderName] = useState("");
  const [showNewFolderDialog, setShowNewFolderDialog] = useState(false);
  const [showFilterPopover, setShowFilterPopover] = useState(false);
  const [filters, setFilters] =
    useState<WorkflowGalleryFilters>(DEFAULT_FILTERS);

  useEffect(() => {
    let isMounted = true;

    const load = async (forceRefresh = false) => {
      if (isMounted) {
        setIsLoadingWorkflows(true);
      }
      try {
        const items = await listWorkflows({ forceRefresh });
        if (isMounted) {
          setWorkflows(items);
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

  const templates = useMemo(() => GALLERY_TEMPLATE_WORKFLOWS, []);
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
  }, [
    isTemplateView,
    searchableTemplates,
    searchableWorkflows,
    selectedTab,
  ]);

  const sortedWorkflows = useMemo(() => {
    return [...filteredWorkflows].sort((a, b) => {
      if (sortBy === "name") {
        return a.name.localeCompare(b.name);
      }
      if (sortBy === "updated") {
        return (
          new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
        );
      }
      if (sortBy === "created") {
        return (
          new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
        );
      }
      return 0;
    });
  }, [filteredWorkflows, sortBy]);

  return {
    searchQuery,
    setSearchQuery,
    selectedTab,
    setSelectedTab,
    sortBy,
    setSortBy,
    newFolderName,
    setNewFolderName,
    showNewFolderDialog,
    setShowNewFolderDialog,
    showFilterPopover,
    setShowFilterPopover,
    filters,
    setFilters,
    isLoadingWorkflows,
    sortedWorkflows,
    tabCounts,
    isTemplateView,
    templates,
  };
};
