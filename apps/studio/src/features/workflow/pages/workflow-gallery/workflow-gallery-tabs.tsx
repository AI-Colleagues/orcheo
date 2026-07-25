import { ChangeEvent, useState } from "react";
import { Input } from "@/design-system/ui/input";
import { Button } from "@/design-system/ui/button";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/design-system/ui/tabs";
import { Loader2, Plus, Search, Upload, Zap } from "lucide-react";
import { type Workflow } from "@features/workflow/data/workflow-data";
import { formatCandidateGroupName } from "@features/workflow/data/templates/candidate-badges";
import { type ApiTeam } from "@features/workflow/lib/workflow-storage-api";
import { UploadWorkflowDialog } from "@features/workflow/components/dialogs/upload-workflow-dialog";
import { useUploadsAllowed } from "@/hooks/use-uploads-allowed";
import { WorkflowCard } from "./workflow-card";
import { TeamSection } from "./team-section";
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
  teams?: ApiTeam[];
  workspaceLabel: string;
  searchQuery: string;
  onSearchQueryChange: (value: string) => void;
  onImportStarterPack: () => void;
  onOpenWorkflow: (workflowId: string, teamSlug?: string) => void;
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
  onDeleteTeam?: (teamId: string) => void;
  onCreateTeam?: () => void;
}

export const WorkflowGalleryTabs = ({
  selectedTab,
  onSelectedTabChange,
  isLoading,
  sortedWorkflows,
  tabCounts,
  isTemplateView,
  teams = [],
  workspaceLabel,
  searchQuery,
  onSearchQueryChange,
  onImportStarterPack,
  onOpenWorkflow,
  onUseTemplate,
  onExportWorkflow,
  onDeleteWorkflow,
  onUpdateCandidateWorkflow,
  onDeleteTeam,
  onCreateTeam,
}: WorkflowGalleryTabsProps) => {
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const uploadsAllowed = useUploadsAllowed();

  const handleSearchChange = (event: ChangeEvent<HTMLInputElement>) => {
    onSearchQueryChange(event.target.value);
  };

  const renderGrid = (items: Workflow[], teamSlug?: string) => (
    <div className="grid grid-cols-1 gap-3 pb-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
      {items.map((workflow) => (
        <WorkflowCard
          key={workflow.id}
          workflow={workflow}
          isTemplate={isTemplateView}
          teamSlug={teamSlug}
          workspaceLabel={workspaceLabel}
          onOpenWorkflow={(id) => onOpenWorkflow(id, teamSlug)}
          onUseTemplate={onUseTemplate}
          onExportWorkflow={onExportWorkflow}
          onDeleteWorkflow={onDeleteWorkflow}
          onUpdateCandidateWorkflow={onUpdateCandidateWorkflow}
        />
      ))}
    </div>
  );

  // Group candidates into vertical, collapsible sections that mirror the
  // grouping of the colleague-candidates repository (one level under
  // `colleagues/`). Independent candidates — those that live directly under
  // `colleagues/` — stay in a flat grid above the grouped sections.
  const renderCandidates = () => {
    const independents: Workflow[] = [];
    const byGroup = new Map<string, Workflow[]>();
    for (const workflow of sortedWorkflows) {
      const group = workflow.candidateGroup;
      if (!group) {
        independents.push(workflow);
        continue;
      }
      const bucket = byGroup.get(group);
      if (bucket) {
        bucket.push(workflow);
      } else {
        byGroup.set(group, [workflow]);
      }
    }

    // Nothing is grouped — keep the flat grid.
    if (byGroup.size === 0) {
      return renderGrid(sortedWorkflows);
    }

    return (
      <div className="flex flex-col gap-1 pb-6">
        {independents.length > 0 ? renderGrid(independents) : null}
        {[...byGroup.entries()]
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([slug, items]) => (
            <TeamSection
              key={slug}
              name={formatCandidateGroupName(slug)}
              count={items.length}
            >
              {renderGrid(items)}
            </TeamSection>
          ))}
      </div>
    );
  };

  // Group colleagues into vertical, collapsible team sections. Candidates are
  // grouped to mirror the colleague-candidates repository layout instead.
  const renderColleagues = () => {
    if (isTemplateView) {
      return renderCandidates();
    }

    // No teams yet — fall back to flat grid (e.g. during first load).
    if (teams.length === 0) {
      return renderGrid(sortedWorkflows);
    }

    const byTeam = new Map<string, Workflow[]>();
    for (const workflow of sortedWorkflows) {
      const key = workflow.teamId ?? "__none__";
      const bucket = byTeam.get(key);
      if (bucket) {
        bucket.push(workflow);
      } else {
        byTeam.set(key, [workflow]);
      }
    }

    // All known teams, in default-first order from the server, then any
    // orphaned workflows whose team no longer appears in the list.
    const leftovers = [...byTeam.keys()].filter(
      (key) => key !== "__none__" && !teams.some((t) => t.id === key),
    );
    const sections = [
      ...teams.map((team) => ({
        key: team.id,
        name: team.name,
        slug: team.slug,
      })),
      ...leftovers.map((key) => ({ key, name: "Other", slug: undefined })),
      ...(byTeam.has("__none__")
        ? [{ key: "__none__", name: "Ungrouped", slug: undefined }]
        : []),
    ];

    return (
      <div className="flex flex-col gap-1 pb-6">
        {sections.map((section) => {
          const items = byTeam.get(section.key) ?? [];
          return (
            <TeamSection
              key={section.key}
              name={section.name}
              count={items.length}
              onRemove={
                onDeleteTeam && section.slug !== undefined
                  ? () => onDeleteTeam(section.key)
                  : undefined
              }
            >
              {items.length > 0 ? renderGrid(items, section.slug) : null}
            </TeamSection>
          );
        })}
      </div>
    );
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
              <span>AI Teams</span>
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
        ) : sortedWorkflows.length === 0 &&
          (isTemplateView || teams.length === 0) ? (
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
          renderColleagues()
        )}

        {!isTemplateView && (
          <div className="mt-4">
            <Button variant="ghost" size="sm" onClick={onCreateTeam}>
              <Plus className="mr-2 h-4 w-4" />
              New team
            </Button>
          </div>
        )}
      </TabsContent>
    </Tabs>
  );
};
