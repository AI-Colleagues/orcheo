import { useEffect } from "react";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/design-system/ui/tabs";
import useCredentialVault from "@/hooks/use-credential-vault";
import { usePageContext } from "@/hooks/use-page-context";
import ServiceTokens from "@features/account/pages/service-tokens";
import WorkspaceMembers from "@features/account/pages/workspace-members";
import TopNavigation from "@features/shared/components/top-navigation";

export default function WorkspaceManagement() {
  const { setPageContext } = usePageContext();
  useEffect(() => {
    setPageContext({ page: "workspace" });
  }, [setPageContext]);

  const {
    credentials,
    isLoading: isCredentialsLoading,
    onAddCredential,
    onUpdateCredential,
    onDeleteCredential,
    onRevealCredentialSecret,
  } = useCredentialVault();

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

      <main className="flex-1 min-h-0 overflow-auto">
        <div className="mx-auto flex w-full max-w-7xl flex-col space-y-6 p-8 pt-6">
          <h1 className="text-3xl font-bold tracking-tight">
            Workspace Management
          </h1>

          <Tabs defaultValue="members" className="flex flex-col gap-4">
            <TabsList className="self-start">
              <TabsTrigger value="members">Workspace Members</TabsTrigger>
              <TabsTrigger value="api-keys">API Keys</TabsTrigger>
            </TabsList>

            <TabsContent value="members">
              <WorkspaceMembers />
            </TabsContent>
            <TabsContent value="api-keys">
              <ServiceTokens />
            </TabsContent>
          </Tabs>
        </div>
      </main>
    </div>
  );
}
