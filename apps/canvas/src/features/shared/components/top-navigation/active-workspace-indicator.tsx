import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Check, ChevronsUpDown, Plus } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/design-system/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/design-system/ui/dropdown-menu";
import { Input } from "@/design-system/ui/input";
import { Label } from "@/design-system/ui/label";
import { toast } from "@/hooks/use-toast";
import {
  createWorkspace,
  getMyWorkspaces,
  type WorkspaceMembershipSummary,
} from "@/lib/api";
import { slugifyWorkspace } from "@/lib/workspace-slug";
import { cn } from "@/lib/utils";
import {
  clearSelectedWorkspaceSlug,
  getSelectedWorkspaceSlug,
  setSelectedWorkspaceSlug,
} from "@/lib/workspace-session";
import {
  getWorkspaceGalleryPath,
  getWorkspacePathWithSlug,
  getWorkspaceSlugFromPathname,
} from "@/lib/workspace-routing";
import { getAuthenticatedUserProfile } from "@features/auth/lib/auth-session";

export default function ActiveWorkspaceIndicator() {
  const authUser = useMemo(() => getAuthenticatedUserProfile(), []);
  const navigate = useNavigate();
  const [workspaces, setWorkspaces] = useState<WorkspaceMembershipSummary[]>(
    [],
  );
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceSlug, setWorkspaceSlugState] = useState("");
  const [workspaceSlugIsManual, setWorkspaceSlugIsManual] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const { pathname } = useLocation();
  const routeWorkspaceSlug = useMemo(
    () => getWorkspaceSlugFromPathname(pathname),
    [pathname],
  );
  const suggestedWorkspaceName = useMemo(() => {
    if (!authUser?.name) {
      return "";
    }
    return `${authUser.name}'s workspace`;
  }, [authUser]);

  useEffect(() => {
    let active = true;

    const load = async () => {
      try {
        const membershipsPayload = await getMyWorkspaces();
        if (!active) {
          return;
        }
        setWorkspaces(membershipsPayload.memberships);

        const currentSlug = routeWorkspaceSlug ?? getSelectedWorkspaceSlug();
        if (membershipsPayload.memberships.length === 0) {
          setWorkspaceName((current) => current || suggestedWorkspaceName);
          setWorkspaceSlugState("");
          setWorkspaceSlugIsManual(false);
          if (currentSlug) {
            clearSelectedWorkspaceSlug();
          }
          return;
        }

        const nextSelected =
          membershipsPayload.memberships.find(
            (workspace) => workspace.slug === currentSlug,
          ) ??
          membershipsPayload.memberships[0] ??
          null;

        if (nextSelected === null) {
          if (currentSlug) {
            clearSelectedWorkspaceSlug();
          }
          return;
        }

        if (nextSelected.slug !== currentSlug) {
          setSelectedWorkspaceSlug(nextSelected.slug);
          if (currentSlug) {
            navigate(
              routeWorkspaceSlug
                ? getWorkspacePathWithSlug(pathname, nextSelected.slug)
                : getWorkspaceGalleryPath(nextSelected.slug),
              { replace: true },
            );
          }
        }
      } catch (error) {
        if (active) {
          setWorkspaces([]);
          if (getSelectedWorkspaceSlug()) {
            clearSelectedWorkspaceSlug();
          }
          if (error instanceof Error) {
            console.error("Failed to load workspaces", error);
          }
        }
      }
    };

    void load();

    return () => {
      active = false;
    };
  }, [navigate, pathname, routeWorkspaceSlug, suggestedWorkspaceName]);

  useEffect(() => {
    if (workspaceSlugIsManual) {
      return;
    }
    setWorkspaceSlugState(slugifyWorkspace(workspaceName));
  }, [workspaceName, workspaceSlugIsManual]);

  const selectedWorkspaceSlug = routeWorkspaceSlug ?? getSelectedWorkspaceSlug();
  const currentWorkspace = selectedWorkspaceSlug
    ? workspaces.find((workspace) => workspace.slug === selectedWorkspaceSlug) ??
      null
    : workspaces[0] ?? null;

  const handleSelectWorkspace = (slug: string) => {
    setSelectedWorkspaceSlug(slug);
    window.location.assign(getWorkspaceGalleryPath(slug));
  };

  const handleCreateWorkspace = async () => {
    const name = workspaceName.trim();
    const slug = workspaceSlug.trim() || slugifyWorkspace(name);

    if (!name || !slug) {
      toast({
        title: "Workspace details required",
        description: "Provide both a name and a slug for the new workspace.",
        variant: "destructive",
      });
      return;
    }

    setIsCreating(true);
    try {
      const created = await createWorkspace({ name, slug });
      toast({
        title: "Workspace created",
        description: `"${created.name}" is ready.`,
      });
      setCreateDialogOpen(false);
      setWorkspaceName("");
      setWorkspaceSlugState("");
      setWorkspaceSlugIsManual(false);
      setSelectedWorkspaceSlug(created.slug);
      window.location.assign(getWorkspaceGalleryPath(created.slug));
    } catch (error) {
      toast({
        title: "Failed to create workspace",
        description:
          error instanceof Error ? error.message : "Unknown error occurred",
        variant: "destructive",
      });
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(
              "inline-flex h-9 w-fit items-center gap-2 whitespace-nowrap rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm ring-offset-background",
              "focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50 [&>span]:line-clamp-1",
            )}
          >
            <span className="truncate">Workspace</span>
            <ChevronsUpDown className="h-4 w-4 opacity-50" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-72">
          {workspaces.length > 0 ? (
            workspaces.map((workspace) => (
              <DropdownMenuItem
                key={workspace.workspace_id}
                onClick={() => {
                  handleSelectWorkspace(workspace.slug);
                }}
                className="flex items-center justify-between"
              >
                <span className="font-medium">{workspace.name}</span>
                {workspace.slug === currentWorkspace?.slug ? (
                  <Check className="h-4 w-4" />
                ) : null}
              </DropdownMenuItem>
            ))
          ) : (
            <DropdownMenuItem disabled>
              No workspaces available
            </DropdownMenuItem>
          )}
          <DropdownMenuItem
            onClick={() => {
              setCreateDialogOpen(true);
            }}
          >
            <Plus className="mr-2 h-4 w-4" />
            Create workspace
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create workspace</DialogTitle>
            <DialogDescription>
              New workspaces inherit the current user as owner and are isolated
              from the active workspace.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="workspace-name">Name</Label>
              <Input
                id="workspace-name"
                value={workspaceName}
                onChange={(event) => setWorkspaceName(event.target.value)}
                placeholder="Acme"
                autoFocus
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="workspace-slug">Workspace URL name</Label>
              <Input
                id="workspace-slug"
                value={workspaceSlug}
                onChange={(event) => {
                  const nextValue = event.target.value;
                  setWorkspaceSlugState(nextValue);
                  setWorkspaceSlugIsManual(nextValue.trim().length > 0);
                }}
                placeholder="acme"
              />
              <p className="text-xs text-muted-foreground">
                Used in links to your workspace. Keep it short and easy to
                share.
              </p>
            </div>
            <Button onClick={handleCreateWorkspace} disabled={isCreating}>
              {isCreating ? "Creating…" : "Create workspace"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
