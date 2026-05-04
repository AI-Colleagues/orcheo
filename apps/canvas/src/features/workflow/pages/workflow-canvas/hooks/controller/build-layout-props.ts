import type { ReadinessTabContentProps } from "@features/workflow/pages/workflow-canvas/components/readiness-tab-content";
import type { SettingsTabContentProps } from "@features/workflow/pages/workflow-canvas/components/settings-tab-content";
import type { TraceTabContentProps } from "@features/workflow/pages/workflow-canvas/components/trace-tab-content";
import type { WorkflowTabContentProps } from "@features/workflow/pages/workflow-canvas/components/workflow-tab-content";
import type {
  ChatKitStartScreenPrompt,
  ChatKitSupportedModel,
  WorkflowVersionRecord,
} from "@features/workflow/lib/workflow-storage.types";
import type { WorkflowCanvasCore } from "./use-workflow-canvas-core";
import type { WorkflowCanvasResources } from "./use-workflow-canvas-resources";
import type { WorkflowCanvasExecution } from "./use-workflow-canvas-execution";

const nodeHasCronTrigger = (
  node: WorkflowCanvasCore["history"]["nodes"][number],
): boolean => {
  if (typeof node.data?.backendType === "string") {
    return node.data.backendType === "CronTriggerNode";
  }

  if (typeof node.data?.iconKey === "string") {
    return node.data.iconKey.toLowerCase() === "schedule";
  }

  if (typeof node.data?.type === "string") {
    return node.data.type.toLowerCase() === "crontriggernode";
  }

  return node.id.toLowerCase().includes("schedule-trigger");
};

export const hasSchedulableCronTrigger = (
  nodes: WorkflowCanvasCore["history"]["nodes"],
  versions: WorkflowVersionRecord[],
): boolean => {
  if (nodes.some(nodeHasCronTrigger)) {
    return true;
  }

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
      path: string[];
      onNameChange?: (name: string) => void;
    };
    credentials: WorkflowCanvasResources["credentials"]["credentials"];
    isCredentialsLoading: boolean;
    onAddCredential: WorkflowCanvasResources["credentials"]["handleAddCredential"];
    onUpdateCredential: WorkflowCanvasResources["credentials"]["handleUpdateCredential"];
    onDeleteCredential: WorkflowCanvasResources["credentials"]["handleDeleteCredential"];
    onRevealCredentialSecret: WorkflowCanvasResources["credentials"]["handleRevealCredentialSecret"];
  };
  tabsProps: {
    activeTab: string;
    onTabChange: (value: string) => void;
    readinessAlertCount: number;
  };
  workflowProps: WorkflowTabContentProps;
  traceProps: TraceTabContentProps;
  readinessProps: ReadinessTabContentProps;
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
  core: WorkflowCanvasCore,
  resources: WorkflowCanvasResources,
  execution: WorkflowCanvasExecution,
): WorkflowLayoutProps {
  const workflowProps: WorkflowTabContentProps = {
    workflowId: core.metadata.currentWorkflowId,
    workflowName: core.metadata.workflowName,
    versions: core.metadata.workflowVersions ?? [],
    isLoading: core.metadata.isWorkflowLoading,
    loadError: core.metadata.workflowLoadError,
    isRunPending: execution.isRunPending,
    onRunWorkflow: execution.handleRunPersistedWorkflow,
    onSaveConfig: resources.saver.handleSaveWorkflowConfig,
    hasCronTriggerNode: hasSchedulableCronTrigger(
      core.history.nodes,
      core.metadata.workflowVersions ?? [],
    ),
    initialIsPublished: core.metadata.isWorkflowPublic,
    initialShareUrl: core.metadata.workflowShareUrl,
  };

  const traceProps: TraceTabContentProps = {
    error: execution.trace.error,
    viewerData: execution.trace.viewerData,
    activeViewer: execution.trace.activeTraceViewer,
    onRefresh: () => execution.trace.refresh(),
    isRefreshing: execution.trace.isRefreshing,
    onSelectTrace: (traceId) => core.execution.setActiveExecutionId(traceId),
  };

  const readinessProps: ReadinessTabContentProps = {
    subworkflows: core.subworkflowState.subworkflows,
    onCreateSubworkflow: execution.handleCreateSubworkflow,
    onInsertSubworkflow: execution.handleInsertSubworkflow,
    onDeleteSubworkflow: execution.handleDeleteSubworkflow,
    validationErrors: core.validation.validationErrors,
    onRunValidation: execution.runPublishValidation,
    onDismissValidation: execution.handleDismissValidation,
    onFixValidation: execution.handleFixValidation,
    isValidating: core.validation.isValidating,
    lastValidationRun: core.validation.lastValidationRun,
  };

  const settingsProps: SettingsTabContentProps = {
    workflowId: core.metadata.currentWorkflowId,
    workflowName: core.metadata.workflowName,
    workflowDescription: core.metadata.workflowDescription,
    workflowTags: core.metadata.workflowTags,
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
        path: ["Projects", "Workflows"],
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
      readinessAlertCount: core.validation.validationErrors.length,
    },
    workflowProps,
    traceProps,
    readinessProps,
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
