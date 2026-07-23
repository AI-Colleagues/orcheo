import React from "react";
import { Tabs, TabsContent } from "@/design-system/ui/tabs";

import WorkflowTabs from "@features/workflow/components/panels/workflow-tabs";
import { StudioChatBubble } from "@features/chatkit/components/studio-chat-bubble";
import type { SettingsTabContentProps } from "@features/workflow/pages/workflow/components/settings-tab-content";
import type { WorkflowTabContentProps } from "@features/workflow/pages/workflow/components/workflow-tab-content";

import { TraceTabContent } from "@features/workflow/pages/workflow/components/trace-tab-content";
import { SettingsTabContent } from "@features/workflow/pages/workflow/components/settings-tab-content";
import { WorkflowTabContent } from "@features/workflow/pages/workflow/components/workflow-tab-content";
import type {
  ChatKitStartScreenPrompt,
  ChatKitSupportedModel,
} from "@features/workflow/lib/workflow-storage.types";
import type {
  Credential,
  CredentialInput,
  CredentialUpdateInput,
} from "@features/workflow/types/credential-vault";

interface ChatState {
  isChatOpen: boolean;
  chatTitle: string;
  user: {
    id: string;
    name: string;
    avatar: string;
  };
  ai: {
    id: string;
    name: string;
    avatar: string;
  };
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
}

interface WorkflowLayoutProps {
  headerProps: {
    currentWorkflow: {
      name: string;
      onNameChange?: (name: string) => void;
    };
    credentials: Credential[];
    isCredentialsLoading: boolean;
    onAddCredential?: (credential: CredentialInput) => Promise<void> | void;
    onUpdateCredential?: (
      id: string,
      updates: CredentialUpdateInput,
    ) => Promise<void> | void;
    onDeleteCredential?: (id: string) => Promise<void> | void;
    onRevealCredentialSecret?: (id: string) => Promise<string | null>;
  };
  tabsProps: {
    activeTab: string;
    onTabChange: (value: string) => void;
  };
  workflowProps: WorkflowTabContentProps;
  traceProps: React.ComponentProps<typeof TraceTabContent>;
  settingsProps: SettingsTabContentProps;
  chat: ChatState | null;
}

export function WorkflowLayout({
  headerProps,
  tabsProps,
  workflowProps,
  traceProps,
  settingsProps,
  chat,
}: WorkflowLayoutProps) {
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <WorkflowTabs
        activeTab={tabsProps.activeTab}
        onTabChange={tabsProps.onTabChange}
        currentWorkflow={headerProps.currentWorkflow}
        credentials={headerProps.credentials}
        isCredentialsLoading={headerProps.isCredentialsLoading}
        onAddCredential={headerProps.onAddCredential}
        onUpdateCredential={headerProps.onUpdateCredential}
        onDeleteCredential={headerProps.onDeleteCredential}
        onRevealCredentialSecret={headerProps.onRevealCredentialSecret}
      />

      <div className="flex-1 flex flex-col min-h-0">
        <Tabs
          value={tabsProps.activeTab}
          onValueChange={tabsProps.onTabChange}
          className="w-full flex flex-col flex-1 min-h-0"
        >
          <TabsContent
            value="workflow"
            forceMount
            className="m-0 flex min-h-0 flex-1 flex-col overflow-hidden p-0 data-[state=inactive]:hidden"
          >
            <WorkflowTabContent
              {...workflowProps}
              isActive={tabsProps.activeTab === "workflow"}
            />
          </TabsContent>

          <TabsContent
            value="trace"
            className="m-0 flex min-h-0 w-full min-w-0 flex-1 flex-col overflow-hidden p-4 data-[state=inactive]:hidden"
          >
            {tabsProps.activeTab === "trace" ? (
              <TraceTabContent
                key={`trace-tab-${tabsProps.activeTab}`}
                {...traceProps}
              />
            ) : null}
          </TabsContent>

          <TabsContent value="settings" className="m-0 p-4 overflow-auto">
            {tabsProps.activeTab === "settings" ? (
              <SettingsTabContent {...settingsProps} />
            ) : null}
          </TabsContent>
        </Tabs>
      </div>

      {chat && (
        <StudioChatBubble
          title={headerProps.currentWorkflow.name}
          user={chat.user}
          ai={chat.ai}
          workflowId={chat.workflowId}
          chatkitWorkflowId={chat.chatkitWorkflowId}
          sessionPayload={{
            workflowId: chat.chatkitWorkflowId ?? chat.workflowId,
            workflowLabel: headerProps.currentWorkflow.name,
            chatNodeId: chat.activeChatNodeId,
          }}
          backendBaseUrl={chat.backendBaseUrl}
          startScreenPrompts={chat.startScreenPrompts}
          supportedModels={chat.supportedModels}
          getClientSecret={chat.getClientSecret}
          sessionStatus={chat.sessionStatus}
          sessionError={chat.sessionError}
          onRetry={chat.refreshSession}
          onResponseStart={chat.handleChatResponseStart}
          onResponseEnd={chat.handleChatResponseEnd}
          onClientTool={chat.handleChatClientTool}
          onDismiss={chat.handleCloseChat}
          onOpen={() => chat.setIsChatOpen(true)}
          isExternallyOpen={chat.isChatOpen}
        />
      )}
    </div>
  );
}
