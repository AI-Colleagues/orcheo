import type { SettingsTabContentProps } from "@features/workflow/pages/workflow/components/settings-tab-content";
import type { TraceTabContentProps } from "@features/workflow/pages/workflow/components/trace-tab-content";
import type { WorkflowTabContentProps } from "@features/workflow/pages/workflow/components/workflow-tab-content";
import type {
  ChatKitStartScreenPrompt,
  ChatKitSupportedModel,
  WorkflowVersionRecord,
} from "@features/workflow/lib/workflow-storage.types";
import type { WorkflowCore } from "./use-workflow-core";
import type { WorkflowResources } from "./use-workflow-resources";
import type { WorkflowExecutionController } from "./use-workflow-execution-controller";

export const hasSchedulableCronTrigger = (
  versions: WorkflowVersionRecord[],
): boolean => {
  const latestVersion = versions.reduce<WorkflowVersionRecord | undefined>(
    (latest, current) =>
      !latest || current.versionNumber > latest.versionNumber
        ? current
        : latest,
    undefined,
  );

  return latestVersion?.hasCronTrigger === true;
};

export interface WorkflowLayoutProps {
  topNavigationProps: {
    currentWorkflow: {
      name: string;
      onNameChange?: (name: string) => void;
    };
    credentials: WorkflowResources["credentials"]["credentials"];
    isCredentialsLoading: boolean;
    onAddCredential: WorkflowResources["credentials"]["handleAddCredential"];
    onUpdateCredential: WorkflowResources["credentials"]["handleUpdateCredential"];
    onDeleteCredential: WorkflowResources["credentials"]["handleDeleteCredential"];
    onRevealCredentialSecret: WorkflowResources["credentials"]["handleRevealCredentialSecret"];
  };
  tabsProps: {
    activeTab: string;
    onTabChange: (value: string) => void;
  };
  workflowProps: WorkflowTabContentProps;
  traceProps: TraceTabContentProps;
  settingsProps: SettingsTabContentProps;
  chat: {
    isChatOpen: boolean;
    chatTitle: string;
    user: { id: string; name: string; avatar: string };
    ai: { id: string; name: string; avatar: string };
    activeChatNodeId: string | null;
    workflowId: string | null;
    chatkitWorkflowId: string | null;
    backendBaseUrl: string | null;
    startScreenPrompts: ChatKitStartScreenPrompt[] | null;
    supportedModels: ChatKitSupportedModel[] | null;
    handleChatResponseStart: () => void;
    handleChatResponseEnd: () => void;
    handleChatClientTool: (tool: {
      name: string;
      params: Record<string, unknown>;
    }) => Promise<Record<string, unknown>>;
    getClientSecret: (currentSecret: string | null) => Promise<string>;
    refreshSession: () => Promise<string>;
    sessionStatus: "idle" | "loading" | "ready" | "error";
    sessionError: string | null;
    handleCloseChat: () => void;
    setIsChatOpen: (open: boolean) => void;
  } | null;
}

export function buildWorkflowLayoutProps(
  core: WorkflowCore,
  resources: WorkflowResources,
  execution: WorkflowExecutionController,
): WorkflowLayoutProps {
  const activeExecution = core.execution.executions.find(
    (execution) => execution.id === core.execution.activeExecutionId,
  );

  const workflowProps: WorkflowTabContentProps = {
    workflowId: core.metadata.currentWorkflowId,
    workflowRouteRef: core.routeWorkflowRef,
    workflowName: core.metadata.workflowName,
    versions: core.metadata.workflowVersions ?? [],
    uploadError: core.metadata.workflowUploadError,
    isLoading: core.metadata.isWorkflowLoading,
    loadError: core.metadata.workflowLoadError,
    isRunPending: execution.isRunPending,
    isRunning: core.execution.isRunning,
    lastRunStatus: activeExecution?.status ?? null,
    onRunWorkflow: execution.handleRunPersistedWorkflow,
    onSaveConfig: resources.saver.handleSaveWorkflowConfig,
    hasCronTriggerNode: hasSchedulableCronTrigger(
      core.metadata.workflowVersions ?? [],
    ),
    initialIsPublished: core.metadata.isWorkflowPublic,
    initialRequireLogin: core.metadata.workflowRequireLogin,
    initialShareUrl: core.metadata.workflowShareUrl,
    missingCredentials: resources.credentialReadiness.missingCredentials,
  };

  const traceProps: TraceTabContentProps = {
    error: execution.trace.error,
    viewerData: execution.trace.viewerData,
    activeViewer: execution.trace.activeTraceViewer,
    onRefresh: () => execution.trace.refresh(),
    isRefreshing: execution.trace.isRefreshing,
    onSelectTrace: (traceId) => core.execution.setActiveExecutionId(traceId),
  };

  const settingsProps: SettingsTabContentProps = {
    workflowId: core.metadata.currentWorkflowId,
    workflowName: core.metadata.workflowName,
    workflowDescription: core.metadata.workflowDescription,
    workflowTags: core.metadata.workflowTags,
    missingCredentials: resources.credentialReadiness.missingCredentials,
    onWorkflowNameChange: core.metadata.setWorkflowName,
    onWorkflowDescriptionChange: core.metadata.setWorkflowDescription,
    onTagsChange: resources.saver.handleTagsChange,
    onSaveWorkflowDetails: resources.saver.handleSaveWorkflowDetails,
    isSavingWorkflowDetails: resources.saver.isSavingWorkflowDetails,
    workflowVersions: core.metadata.workflowVersions ?? [],
    onRestoreVersion: resources.saver.handleRestoreVersion,
    listeners: resources.listeners.listeners,
    listenerMetrics: resources.listeners.metrics,
    isListenersLoading: resources.listeners.isLoading,
    isListenersRefreshing: resources.listeners.isRefreshing,
    activeListenerSubscriptionId: resources.listeners.activeSubscriptionId,
    onRefreshListeners: resources.listeners.refreshListeners,
    onPauseListener: resources.listeners.pauseListener,
    onResumeListener: resources.listeners.resumeListener,
  };

  return {
    topNavigationProps: {
      currentWorkflow: {
        name: core.metadata.workflowName,
        onNameChange: core.metadata.setWorkflowName,
      },
      credentials: resources.credentials.credentials,
      isCredentialsLoading: resources.credentials.isCredentialsLoading,
      onAddCredential: resources.credentials.handleAddCredential,
      onUpdateCredential: resources.credentials.handleUpdateCredential,
      onDeleteCredential: resources.credentials.handleDeleteCredential,
      onRevealCredentialSecret:
        resources.credentials.handleRevealCredentialSecret,
    },
    tabsProps: {
      activeTab: core.ui.activeTab,
      onTabChange: core.ui.setActiveTab,
    },
    workflowProps,
    traceProps,
    settingsProps,
    chat: {
      isChatOpen: core.chat.isChatOpen,
      chatTitle: core.chat.chatTitle,
      user: core.user,
      ai: core.ai,
      activeChatNodeId: core.chat.activeChatNodeId,
      workflowId: core.chat.workflowId,
      chatkitWorkflowId: core.metadata.currentWorkflowId,
      backendBaseUrl: core.chat.backendBaseUrl,
      startScreenPrompts: core.metadata.chatkitStartScreenPrompts,
      supportedModels: core.metadata.chatkitSupportedModels,
      handleChatResponseStart: core.chat.handleChatResponseStart,
      handleChatResponseEnd: core.chat.handleChatResponseEnd,
      handleChatClientTool: core.chat.handleChatClientTool,
      getClientSecret: core.chat.getClientSecret,
      refreshSession: core.chat.refreshSession,
      sessionStatus: core.chat.sessionStatus,
      sessionError: core.chat.sessionError,
      handleCloseChat: core.chat.handleCloseChat,
      setIsChatOpen: core.chat.setIsChatOpen,
    },
  };
}
