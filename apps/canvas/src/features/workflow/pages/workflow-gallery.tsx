import { useEffect, useState } from "react";
import TopNavigation from "@features/shared/components/top-navigation";
import { getActiveWorkspace } from "@/lib/api";
import useCredentialVault from "@/hooks/use-credential-vault";
import { usePageContext } from "@/hooks/use-page-context";
import { WorkflowGalleryHeader } from "@/features/workflow/pages/workflow-gallery/workflow-gallery-header";
import { WorkflowGalleryTabs } from "@/features/workflow/pages/workflow-gallery/workflow-gallery-tabs";
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
    handleUseTemplate,
    handleImportStarterPack,
    handleExportWorkflow,
    handleDeleteWorkflow,
    handleOpenWorkflow,
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

      <main className="flex flex-1 min-h-0 flex-col overflow-hidden">
        <WorkflowGalleryHeader />

        <div className="flex-1 overflow-auto">
          <WorkflowGalleryTabs
            selectedTab={selectedTab}
            onSelectedTabChange={setSelectedTab}
            isLoading={isLoadingWorkflows}
            sortedWorkflows={sortedWorkflows}
            tabCounts={tabCounts}
            isTemplateView={isTemplateView}
            workspaceLabel={workspaceLabel}
            searchQuery={searchQuery}
            onSearchQueryChange={setSearchQuery}
            onImportStarterPack={handleImportStarterPack}
            onOpenWorkflow={handleOpenWorkflow}
            onUseTemplate={handleUseTemplate}
            onExportWorkflow={handleExportWorkflow}
            onDeleteWorkflow={handleDeleteWorkflow}
          />
        </div>
      </main>
    </div>
  );
}
