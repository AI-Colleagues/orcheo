import { useWorkflowGalleryActions } from "./use-workflow-gallery-actions";
import { useWorkflowGalleryState } from "./use-workflow-gallery-state";

export const useWorkflowGallery = () => {
  const state = useWorkflowGalleryState();
  const actions = useWorkflowGalleryActions({
    setSelectedTab: state.setSelectedTab,
    teams: state.teams,
    refreshTeams: state.refreshTeams,
  });

  return {
    ...state,
    ...actions,
  };
};
