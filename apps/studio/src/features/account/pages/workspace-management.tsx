import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/design-system/ui/tabs";
import ServiceTokens from "@features/account/pages/service-tokens";
import WorkspaceMembers from "@features/account/pages/workspace-members";

export default function WorkspaceManagement() {
  return (
    <main className="h-full min-h-0 overflow-auto">
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
  );
}
