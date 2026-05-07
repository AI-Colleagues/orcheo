import { useEffect, useMemo, useState } from "react";
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
import { Badge } from "@/design-system/ui/badge";
import { toast } from "@/hooks/use-toast";
import {
  createWorkspace,
  getMyWorkspaces,
  type WorkspaceMembershipSummary,
} from "@/lib/api";
import {
  clearSelectedWorkspaceSlug,
  getSelectedWorkspaceSlug,
  setSelectedWorkspaceSlug,
} from "@/lib/workspace-session";
import { getAuthenticatedUserProfile } from "@features/auth/lib/auth-session";

const slugify = (value: string): string =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");

export default function ActiveWorkspaceIndicator() {
  const authUser = useMemo(() => getAuthenticatedUserProfile(), []);
  const [workspaces, setWorkspaces] = useState<WorkspaceMembershipSummary[]>(
    [],
  );
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceSlug, setWorkspaceSlugState] = useState("");
  const [isCreating, setIsCreating] = useState(false);
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

        const currentSlug = getSelectedWorkspaceSlug();
        if (membershipsPayload.memberships.length === 0) {
          setWorkspaceName((current) => current || suggestedWorkspaceName);
          setWorkspaceSlugState("");
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
            window.location.reload();
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

    const handleSelectionChange = () => {
      void load();
    };
    window.addEventListener(
      "orcheo-workspace-selection-changed",
      handleSelectionChange,
    );

    return () => {
      active = false;
      window.removeEventListener(
        "orcheo-workspace-selection-changed",
        handleSelectionChange,
      );
    };
  }, [suggestedWorkspaceName]);

  const currentWorkspace = useMemo(() => {
    const selectedSlug = getSelectedWorkspaceSlug();
    if (selectedSlug) {
      return (
        workspaces.find((workspace) => workspace.slug === selectedSlug) ?? null
      );
    }
    return workspaces[0] ?? null;
  }, [workspaces]);

  const handleSelectWorkspace = (slug: string) => {
    setSelectedWorkspaceSlug(slug);
    window.location.reload();
  };

  const handleCreateWorkspace = async () => {
    const name = workspaceName.trim();
    const slug = slugify(workspaceSlug || workspaceName);

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
      setSelectedWorkspaceSlug(created.slug);
      window.location.reload();
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
          <Button
            variant="outline"
            className="inline-flex items-center gap-2 border-dashed bg-background/80"
            disabled={false}
          >
            <Badge variant="secondary" className="text-[10px] uppercase">
              Workspace
            </Badge>
            <span className="max-w-[10rem] truncate font-medium">
              {currentWorkspace?.slug ?? "No workspace"}
            </span>
            <ChevronsUpDown className="h-4 w-4 text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72">
          {workspaces.length > 0 ? (
            workspaces.map((workspace) => (
              <DropdownMenuItem
                key={workspace.workspace_id}
                onSelect={(event) => {
                  event.preventDefault();
                  handleSelectWorkspace(workspace.slug);
                }}
                className="flex items-center justify-between"
              >
                <span className="flex flex-col">
                  <span className="font-medium">{workspace.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {workspace.slug}
                  </span>
                </span>
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
            onSelect={(event) => {
              event.preventDefault();
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
              onChange={(event) => setWorkspaceSlugState(event.target.value)}
              placeholder="acme"
            />
            <p className="text-xs text-muted-foreground">
              Used in links to your workspace. Keep it short and easy to share.
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
