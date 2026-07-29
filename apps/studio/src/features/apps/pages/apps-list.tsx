import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import { getWorkspaceAppPath } from "@/lib/workspace-routing";
import { AppCard } from "../components/app-card";
import { ArchiveAppDialog } from "../components/archive-app-dialog";
import { CreateAppDialog } from "../components/create-app-dialog";
import { PublishAppDialog } from "../components/publish-app-dialog";
import {
  archiveApp,
  createApp,
  toggleAppPublish,
  useApps,
} from "../data/apps-store";
import type { HostedApp } from "../data/sample-apps";

export default function AppsList() {
  const { workspaceSlug } = useParams<{ workspaceSlug?: string }>();
  const navigate = useNavigate();
  const { apps, loading, error } = useApps(workspaceSlug);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<HostedApp | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [publishTarget, setPublishTarget] = useState<HostedApp | null>(null);
  const [publishing, setPublishing] = useState(false);
  const openApp = (app: HostedApp) => {
    navigate(getWorkspaceAppPath(workspaceSlug, app.id));
  };

  const handleArchive = async () => {
    if (!archiveTarget) return;
    setActionError(null);
    setArchiving(true);
    try {
      await archiveApp(archiveTarget.id);
      setArchiveTarget(null);
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "Unable to delete app.",
      );
    } finally {
      setArchiving(false);
    }
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

        {actionError ? (
          <p className="text-sm text-destructive">{actionError}</p>
        ) : null}

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
                onArchiveApp={setArchiveTarget}
                onTogglePublish={(target) => {
                  setActionError(null);
                  if (target.state !== "published") {
                    setPublishTarget(target);
                    return;
                  }
                  void toggleAppPublish(target).catch(
                    (toggleError: unknown) => {
                      setActionError(
                        toggleError instanceof Error
                          ? toggleError.message
                          : "Unable to update publication state.",
                      );
                    },
                  );
                }}
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
      <ArchiveAppDialog
        open={archiveTarget !== null}
        appName={archiveTarget?.name ?? ""}
        isPending={archiving}
        onOpenChange={(open) => {
          if (!open && !archiving) setArchiveTarget(null);
        }}
        onConfirm={handleArchive}
      />
      <PublishAppDialog
        open={publishTarget !== null}
        isPending={publishing}
        onOpenChange={(open) => {
          if (!open && !publishing) setPublishTarget(null);
        }}
        onConfirm={async (visibility) => {
          if (!publishTarget) return;
          setActionError(null);
          setPublishing(true);
          try {
            await toggleAppPublish(publishTarget, visibility);
            setPublishTarget(null);
          } catch (error) {
            setActionError(
              error instanceof Error
                ? error.message
                : "Unable to update publication state.",
            );
          } finally {
            setPublishing(false);
          }
        }}
      />
    </main>
  );
}
