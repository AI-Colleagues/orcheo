import { useEffect } from "react";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/design-system/ui/tabs";
import useCredentialVault from "@/hooks/use-credential-vault";
import { usePageContext } from "@/hooks/use-page-context";
import AgentSettingsTab from "@features/account/components/settings/agent-settings-tab";
import AppearanceSettingsTab from "@features/account/components/settings/appearance-settings-tab";
import TopNavigation from "@features/shared/components/top-navigation";

export default function Settings() {
  const { setPageContext } = usePageContext();
  useEffect(() => {
    setPageContext({ page: "settings" });
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
        <div className="mx-auto flex w-full max-w-7xl flex-col space-y-4 p-8 pt-6">
          <div className="flex items-center justify-between space-y-2">
            <h2 className="text-3xl font-bold tracking-tight">Settings</h2>
          </div>
          <Tabs defaultValue="appearance" className="space-y-4">
            <TabsList>
              <TabsTrigger value="appearance">Appearance</TabsTrigger>
              <TabsTrigger value="agent">Agents</TabsTrigger>
            </TabsList>
            <TabsContent value="appearance" className="space-y-4">
              <AppearanceSettingsTab />
            </TabsContent>
            <TabsContent value="agent" className="space-y-4">
              <AgentSettingsTab />
            </TabsContent>
          </Tabs>
        </div>
      </main>
    </div>
  );
}
