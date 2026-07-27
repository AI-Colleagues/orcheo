import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Avatar, AvatarFallback, AvatarImage } from "@/design-system/ui/avatar";
import { Button } from "@/design-system/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuPortal,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/design-system/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/design-system/ui/dialog";
import { Input } from "@/design-system/ui/input";
import { Label } from "@/design-system/ui/label";
import { toast } from "@/hooks/use-toast";
import {
  Building2,
  Check,
  ChevronsUpDown,
  Info,
  LogOut,
  Plus,
  Settings,
  User,
} from "lucide-react";
import { getAuthenticatedUserProfile } from "@features/auth/lib/auth-session";
import { logoutSession } from "@features/auth/lib/auth-api";
import {
  createWorkspace,
  getMyWorkspaces,
  type WorkspaceMembershipSummary,
} from "@/lib/api";
import { slugifyWorkspace } from "@/lib/workspace-slug";
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
import AboutDialog from "./about-dialog";

export default function ProfileMenu() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const authUser = useMemo(() => getAuthenticatedUserProfile(), []);
  const accountLabel = authUser?.name ?? "Account";
  const accountInitials = accountLabel
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const [workspaces, setWorkspaces] = useState<WorkspaceMembershipSummary[]>(
    [],
  );
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceSlug, setWorkspaceSlugState] = useState("");
  const [workspaceSlugIsManual, setWorkspaceSlugIsManual] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

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

  const selectedWorkspaceSlug =
    routeWorkspaceSlug ?? getSelectedWorkspaceSlug();
  const currentWorkspace = selectedWorkspaceSlug
    ? (workspaces.find(
        (workspace) => workspace.slug === selectedWorkspaceSlug,
      ) ?? null)
    : (workspaces[0] ?? null);

  const handleSelectWorkspace = useCallback((slug: string) => {
    setSelectedWorkspaceSlug(slug);
    window.location.assign(getWorkspaceGalleryPath(slug));
  }, []);

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
            className="flex w-full items-center gap-2 rounded-md p-2 text-left hover:bg-accent"
          >
            <Avatar className="h-8 w-8 shrink-0">
              {authUser?.avatar ? (
                <AvatarImage src={authUser.avatar} alt={authUser.name} />
              ) : null}
              <AvatarFallback>
                {accountInitials || <User className="h-4 w-4" />}
              </AvatarFallback>
            </Avatar>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-foreground">
                {accountLabel}
              </div>
              <div className="truncate text-xs text-muted-foreground">
                {currentWorkspace?.name ?? "Workspace"}
              </div>
            </div>
            <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" />
            {updateAvailable ? (
              <span
                className="h-2 w-2 shrink-0 rounded-full bg-warning"
                aria-label="Update available"
                title="Update available"
              />
            ) : null}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" side="top" className="w-56">
          <DropdownMenuLabel>My account</DropdownMenuLabel>
          <DropdownMenuItem asChild>
            <Link to="/profile" className="flex w-full items-center gap-0">
              <User className="mr-2 h-4 w-4" />
              <span>Profile</span>
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link to="/settings" className="flex w-full items-center gap-0">
              <Settings className="mr-2 h-4 w-4" />
              <span>Settings</span>
            </Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>Workspace</DropdownMenuLabel>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <Building2 className="mr-2 h-4 w-4" />
              <span>{currentWorkspace?.name ?? "Select workspace"}</span>
            </DropdownMenuSubTrigger>
            <DropdownMenuPortal>
              <DropdownMenuSubContent className="w-56">
                {workspaces.length > 0 ? (
                  workspaces.map((workspace) => (
                    <div
                      key={workspace.workspace_id}
                      className="flex items-center"
                    >
                      <DropdownMenuItem
                        onSelect={() => handleSelectWorkspace(workspace.slug)}
                        className="min-w-0 flex-1 justify-between"
                      >
                        <span className="truncate font-medium">
                          {workspace.name}
                        </span>
                        {workspace.slug === currentWorkspace?.slug ? (
                          <Check className="h-4 w-4 shrink-0" />
                        ) : null}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        aria-label={`Manage ${workspace.name}`}
                        title={`Manage ${workspace.name}`}
                        className="w-8 shrink-0 justify-center px-0"
                        onSelect={() =>
                          navigate(`/${workspace.slug}/workspace`)
                        }
                      >
                        <Settings className="h-3.5 w-3.5" />
                      </DropdownMenuItem>
                    </div>
                  ))
                ) : (
                  <DropdownMenuItem disabled>
                    No workspaces available
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => setCreateDialogOpen(true)}>
                  <Plus className="mr-2 h-4 w-4" />
                  Create workspace
                </DropdownMenuItem>
              </DropdownMenuSubContent>
            </DropdownMenuPortal>
          </DropdownMenuSub>
          <DropdownMenuSeparator />
          <DropdownMenuItem asChild>
            <button
              type="button"
              className="flex w-full items-center gap-0"
              onClick={() => setAboutOpen(true)}
            >
              <Info className="mr-2 h-4 w-4" />
              <span>About</span>
            </button>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <button
              type="button"
              className="flex w-full items-center gap-0 text-destructive"
              onClick={() => {
                void logoutSession().finally(() => {
                  navigate("/login", { replace: true });
                });
              }}
            >
              <LogOut className="mr-2 h-4 w-4" />
              <span>Log out</span>
            </button>
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

      <AboutDialog
        open={aboutOpen}
        onOpenChange={setAboutOpen}
        onUpdateAvailableChange={setUpdateAvailable}
      />
    </>
  );
}
