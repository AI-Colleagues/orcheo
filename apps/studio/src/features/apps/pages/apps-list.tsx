import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import { usePageContext } from "@/hooks/use-page-context";
import { getWorkspaceAppPath } from "@/lib/workspace-routing";
import { AppCard } from "../components/app-card";
import { CreateAppDialog } from "../components/create-app-dialog";
import { createApp, toggleAppPublish, useApps } from "../data/apps-store";
import type { HostedApp } from "../data/sample-apps";

export default function AppsList() {
  const { workspaceSlug } = useParams<{ workspaceSlug?: string }>();
  const navigate = useNavigate();
  const { apps, loading, error } = useApps(workspaceSlug);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const { setPageContext } = usePageContext();

  useEffect(() => {
    setPageContext({ page: "other" });
  }, [setPageContext]);

  const openApp = (app: HostedApp) => {
    navigate(getWorkspaceAppPath(workspaceSlug, app.id));
  };

  return (
    <main className="flex h-full min-h-0 flex-col overflow-auto p-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Apps
          </h1>
          <Button onClick={() => setIsCreateOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            Create app
          </Button>
        </div>

        {loading ? (
          <p className="text-sm text-muted-foreground">Loading apps…</p>
        ) : error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : apps.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No apps yet. Create one to host a static web app backed by your
            workflows.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {apps.map((app) => (
              <AppCard
                key={app.id}
                app={app}
                onOpen={openApp}
                onTogglePublish={(target) => void toggleAppPublish(target)}
              />
            ))}
          </div>
        )}
      </div>

      <CreateAppDialog
        open={isCreateOpen}
        onOpenChange={setIsCreateOpen}
        onCreate={async (name, alias) => {
          const app = await createApp(name, alias);
          navigate(getWorkspaceAppPath(workspaceSlug, app.id));
        }}
      />
    </main>
  );
}
