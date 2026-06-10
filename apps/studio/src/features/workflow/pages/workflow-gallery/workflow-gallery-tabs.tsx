import { ChangeEvent, useState } from "react";
import { Input } from "@/design-system/ui/input";
import { Button } from "@/design-system/ui/button";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/design-system/ui/tabs";
import { Loader2, Search, Upload, Zap } from "lucide-react";
import { type Workflow } from "@features/workflow/data/workflow-data";
import { UploadWorkflowDialog } from "@features/workflow/components/dialogs/upload-workflow-dialog";
import { useUploadsAllowed } from "@/hooks/use-uploads-allowed";
import { WorkflowCard } from "./workflow-card";
import {
  type WorkflowGalleryTab,
  type WorkflowGalleryTabCounts,
} from "./types";

interface WorkflowGalleryTabsProps {
  selectedTab: WorkflowGalleryTab;
  onSelectedTabChange: (value: WorkflowGalleryTab) => void;
  isLoading: boolean;
  sortedWorkflows: Workflow[];
  tabCounts: WorkflowGalleryTabCounts;
  isTemplateView: boolean;
  workspaceLabel: string;
  searchQuery: string;
  onSearchQueryChange: (value: string) => void;
  onImportStarterPack: () => void;
  onOpenWorkflow: (workflowId: string) => void;
  onUseTemplate: (workflowId: string) => void;
  onExportWorkflow: (workflow: Workflow) => void;
  onDeleteWorkflow: (
    workflowId: string,
    workflowName: string,
  ) => Promise<void> | void;
}

export const WorkflowGalleryTabs = ({
  selectedTab,
  onSelectedTabChange,
  isLoading,
  sortedWorkflows,
  tabCounts,
  isTemplateView,
  workspaceLabel,
  searchQuery,
  onSearchQueryChange,
  onImportStarterPack,
  onOpenWorkflow,
  onUseTemplate,
  onExportWorkflow,
  onDeleteWorkflow,
}: WorkflowGalleryTabsProps) => {
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const uploadsAllowed = useUploadsAllowed();

  const handleSearchChange = (event: ChangeEvent<HTMLInputElement>) => {
    onSearchQueryChange(event.target.value);
  };

  return (
    <Tabs
      value={selectedTab}
      onValueChange={(value) =>
        onSelectedTabChange(value as WorkflowGalleryTab)
      }
      className="px-4 pt-3"
    >
      <div className="mb-3 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:gap-4">
          <TabsList className="flex h-auto flex-wrap justify-start gap-1">
            <TabsTrigger value="all" className="gap-2">
              <span>AI Colleagues</span>
              <span className="text-xs text-muted-foreground">
                {tabCounts.all}
              </span>
            </TabsTrigger>
            <TabsTrigger value="pinned" className="gap-2">
              <span>Starred</span>
              <span className="text-xs text-muted-foreground">
                {tabCounts.pinned}
              </span>
            </TabsTrigger>
            <TabsTrigger value="templates" className="gap-2">
              <span>Candidates</span>
              <span className="text-xs text-muted-foreground">
                {tabCounts.templates}
              </span>
            </TabsTrigger>
          </TabsList>

          <div className="relative w-full md:w-[320px]">
            <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search colleagues..."
              className="pl-10"
              value={searchQuery}
              onChange={handleSearchChange}
            />
          </div>
        </div>
        {uploadsAllowed === true && (
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsUploadOpen(true)}
            >
              <Upload className="mr-2 h-4 w-4" />
              Upload
            </Button>
            <UploadWorkflowDialog
              open={isUploadOpen}
              onOpenChange={setIsUploadOpen}
            />
          </>
        )}
      </div>

      <TabsContent value={selectedTab} className="mt-0">
        {isLoading && !isTemplateView ? (
          <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 text-center">
            <div className="rounded-full bg-muted p-4">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
            <div>
              <h3 className="text-lg font-medium">Loading colleagues</h3>
              <p className="text-sm text-muted-foreground">
                Pulling your workspace from storage.
              </p>
            </div>
          </div>
        ) : sortedWorkflows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <div className="mb-4 rounded-full bg-muted p-4">
              <Zap className="h-8 w-8 text-muted-foreground" />
            </div>
            <h3 className="mb-2 text-lg font-medium">No colleagues found</h3>
            <p className="mb-6 max-w-md text-muted-foreground">
              {searchQuery
                ? `No colleagues match your search for "${searchQuery}"`
                : "Import starter colleagues or onboard candidates to get started."}
            </p>
            <div className="flex flex-col items-center gap-3">
              {!isTemplateView ? (
                <Button variant="outline" onClick={onImportStarterPack}>
                  Import Starter Pack
                </Button>
              ) : null}
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 pb-6 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
            {sortedWorkflows.map((workflow) => (
              <WorkflowCard
                key={workflow.id}
                workflow={workflow}
                isTemplate={isTemplateView}
                workspaceLabel={workspaceLabel}
                onOpenWorkflow={onOpenWorkflow}
                onUseTemplate={onUseTemplate}
                onExportWorkflow={onExportWorkflow}
                onDeleteWorkflow={onDeleteWorkflow}
              />
            ))}
          </div>
        )}
      </TabsContent>
    </Tabs>
  );
};
