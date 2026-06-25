import { useEffect, useState } from "react";
import TopNavigation from "@features/shared/components/top-navigation";
import { getActiveWorkspace } from "@/lib/api";
import useCredentialVault from "@/hooks/use-credential-vault";
import { usePageContext } from "@/hooks/use-page-context";
import { Button } from "@/design-system/ui/button";
import { Plus } from "lucide-react";
import { WorkflowGalleryTabs } from "@/features/workflow/pages/workflow-gallery/workflow-gallery-tabs";
import { OnboardTeamDialog } from "@/features/workflow/pages/workflow-gallery/onboard-team-dialog";
import { CreateTeamDialog } from "@/features/workflow/pages/workflow-gallery/create-team-dialog";
import { useWorkflowGallery } from "@/features/workflow/pages/workflow-gallery/use-workflow-gallery";

export default function WorkflowGallery() {
  const { setPageContext } = usePageContext();
  const [workspaceLabel, setWorkspaceLabel] = useState("AI Colleagues");
  useEffect(() => {
    setPageContext({ page: "gallery" });
  }, [setPageContext]);

  useEffect(() => {
    let active = true;

    const loadWorkspaceLabel = async () => {
      try {
        const workspace = await getActiveWorkspace();
        if (active && workspace.name.trim()) {
          setWorkspaceLabel(workspace.name.trim());
        }
      } catch {
        if (active) {
          setWorkspaceLabel("AI Colleagues");
        }
      }
    };

    void loadWorkspaceLabel();

    return () => {
      active = false;
    };
  }, []);
  const {
    credentials,
    isLoading: isCredentialsLoading,
    onAddCredential,
    onUpdateCredential,
    onDeleteCredential,
    onRevealCredentialSecret,
  } = useCredentialVault();

  const {
    searchQuery,
    setSearchQuery,
    selectedTab,
    setSelectedTab,
    isLoadingWorkflows,
    sortedWorkflows,
    tabCounts,
    isTemplateView,
    teams,
    handleUseTemplate,
    handleImportStarterPack,
    handleExportWorkflow,
    handleDeleteWorkflow,
    handleUpdateCandidateWorkflow,
    handleOpenWorkflow,
    onboardTarget,
    confirmOnboardTeam,
    cancelOnboardTeam,
    isCreateTeamOpen,
    openCreateTeamDialog,
    closeCreateTeamDialog,
    handleCreateTeam,
    handleDeleteTeam,
  } = useWorkflowGallery();

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <TopNavigation
        credentials={credentials}
        isCredentialsLoading={isCredentialsLoading}
        onAddCredential={onAddCredential}
        onUpdateCredential={onUpdateCredential}
        onDeleteCredential={onDeleteCredential}
        onRevealCredentialSecret={onRevealCredentialSecret}
      />

      <main className="relative flex flex-1 min-h-0 flex-col overflow-hidden">
        <div className="flex-1 overflow-auto">
          <WorkflowGalleryTabs
            selectedTab={selectedTab}
            onSelectedTabChange={setSelectedTab}
            isLoading={isLoadingWorkflows}
            sortedWorkflows={sortedWorkflows}
            tabCounts={tabCounts}
            isTemplateView={isTemplateView}
            teams={teams}
            workspaceLabel={workspaceLabel}
            searchQuery={searchQuery}
            onSearchQueryChange={setSearchQuery}
            onImportStarterPack={handleImportStarterPack}
            onOpenWorkflow={handleOpenWorkflow}
            onUseTemplate={handleUseTemplate}
            onExportWorkflow={handleExportWorkflow}
            onDeleteWorkflow={handleDeleteWorkflow}
            onUpdateCandidateWorkflow={handleUpdateCandidateWorkflow}
            onDeleteTeam={handleDeleteTeam}
          />
        </div>

        {!isTemplateView && (
          <div className="absolute bottom-4 left-4">
            <Button variant="outline" size="sm" onClick={openCreateTeamDialog}>
              <Plus className="mr-2 h-4 w-4" />
              New team
            </Button>
          </div>
        )}
      </main>

      <CreateTeamDialog
        open={isCreateTeamOpen}
        onOpenChange={(open) => {
          if (!open) closeCreateTeamDialog();
        }}
        onCreate={handleCreateTeam}
      />

      <OnboardTeamDialog
        open={onboardTarget !== null}
        candidateName={onboardTarget?.candidateName ?? ""}
        teams={teams}
        onSelect={confirmOnboardTeam}
        onOpenChange={(open) => {
          if (!open) {
            cancelOnboardTeam();
          }
        }}
      />
    </div>
  );
}
