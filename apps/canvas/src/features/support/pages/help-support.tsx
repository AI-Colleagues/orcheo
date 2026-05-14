import { useEffect, useMemo, useState } from "react";
import TopNavigation from "@features/shared/components/top-navigation";
import useCredentialVault from "@/hooks/use-credential-vault";
import { usePageContext } from "@/hooks/use-page-context";
import { SupportHeader } from "@features/support/components/support-header";
import { SupportResources } from "@features/support/components/support-resources";
import { SupportHelpTabs } from "@features/support/components/support-help-tabs";
import { SupportChatCallout } from "@features/support/components/support-chat-callout";
import type { ChatMessageProps } from "@features/shared/components/chat-message";

const user = {
  id: "user-1",
  name: "Avery Chen",
  avatar: "https://avatar.vercel.sh/avery",
};

const ai = {
  id: "ai-1",
  name: "Orcheo Support",
  avatar: "https://avatar.vercel.sh/orcheo-support",
  isAI: true,
};

export default function HelpSupport() {
  const { setPageContext } = usePageContext();
  useEffect(() => {
    setPageContext({ page: "help" });
  }, [setPageContext]);
  const [searchQuery, setSearchQuery] = useState("");
  const initialMessages = useMemo<ChatMessageProps[]>(
    () => [
      {
        id: "msg-1",
        content:
          "Hello! I'm the Orcheo support assistant. How can I help you today?",
        sender: ai,
        timestamp: new Date(Date.now() - 60000),
      },
    ],
    [],
  );

  const {
    credentials,
    isLoading: isCredentialsLoading,
    onAddCredential,
    onUpdateCredential,
    onDeleteCredential,
    onRevealCredentialSecret,
  } = useCredentialVault({ actorName: user.name });

  return (
    <div className="flex min-h-screen flex-col">
      <TopNavigation
        credentials={credentials}
        isCredentialsLoading={isCredentialsLoading}
        onAddCredential={onAddCredential}
        onUpdateCredential={onUpdateCredential}
        onDeleteCredential={onDeleteCredential}
        onRevealCredentialSecret={onRevealCredentialSecret}
      />

      <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 p-8 pt-6">
        <SupportHeader
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
        />

        <SupportResources />

        <SupportHelpTabs />

        <SupportChatCallout
          ai={ai}
          user={user}
          initialMessages={initialMessages}
        />
      </main>
    </div>
  );
}
