import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { Input } from "@/design-system/ui/input";
import { Label } from "@/design-system/ui/label";
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

interface WorkspaceBootstrapGateProps {
  children: ReactNode;
}

const slugify = (value: string): string =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");

export function WorkspaceBootstrapGate({
  children,
}: WorkspaceBootstrapGateProps) {
  const authUser = useMemo(() => getAuthenticatedUserProfile(), []);
  const suggestedWorkspaceName = useMemo(() => {
    if (!authUser?.name) {
      return "My workspace";
    }
    return `${authUser.name}'s workspace`;
  }, [authUser]);
  const [workspaces, setWorkspaces] = useState<WorkspaceMembershipSummary[]>(
    [],
  );
  const [isLoading, setIsLoading] = useState(Boolean(authUser));
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceSlug, setWorkspaceSlugState] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  useEffect(() => {
    if (!authUser) {
      setIsLoading(false);
      return;
    }

    let active = true;

    const load = async () => {
      try {
        const payload = await getMyWorkspaces();
        if (!active) {
          return;
        }

        setWorkspaces(payload.memberships);
        const currentSlug = getSelectedWorkspaceSlug();

        if (payload.memberships.length === 0) {
          setWorkspaceName((current) => current || suggestedWorkspaceName);
          setWorkspaceSlugState("");
          if (currentSlug) {
            clearSelectedWorkspaceSlug();
          }
          setIsLoading(false);
          return;
        }

        const selectedWorkspace =
          payload.memberships.find(
            (workspace) => workspace.slug === currentSlug,
          ) ?? payload.memberships[0];

        if (selectedWorkspace) {
          if (selectedWorkspace.slug !== currentSlug) {
            setSelectedWorkspaceSlug(selectedWorkspace.slug);
          }
        } else if (currentSlug) {
          clearSelectedWorkspaceSlug();
        }

        setIsLoading(false);
      } catch (error) {
        if (!active) {
          return;
        }
        console.error("Failed to load workspace memberships", error);
        setIsLoading(false);
      }
    };

    void load();

    return () => {
      active = false;
    };
  }, [authUser, suggestedWorkspaceName]);

  useEffect(() => {
    if (workspaceName.trim()) {
      return;
    }
    setWorkspaceName(suggestedWorkspaceName);
  }, [suggestedWorkspaceName, workspaceName]);

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

  if (!authUser) {
    return <>{children}</>;
  }

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-100 text-foreground dark:bg-slate-950">
        <Card className="w-full max-w-md border-border bg-card/80 backdrop-blur-xl dark:border-primary/25 dark:bg-primary/5">
          <CardContent className="flex items-center gap-3 p-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>Checking workspace access…</span>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (workspaces.length > 0) {
    return <>{children}</>;
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-100 text-foreground dark:bg-slate-950">
      <div
        className="absolute inset-0 bg-gradient-to-br from-slate-100 via-slate-200 to-slate-100 dark:from-slate-950 dark:via-slate-900/80 dark:to-black"
        aria-hidden="true"
      />
      <div
        className="absolute inset-0 opacity-40 mix-blend-soft-light dark:opacity-60"
        style={{
          backgroundImage:
            "radial-gradient(circle at 20% 20%, rgba(148, 163, 184, 0.2), transparent 45%), radial-gradient(circle at 80% 30%, rgba(56, 189, 248, 0.25), transparent 50%), radial-gradient(circle at 50% 80%, rgba(45, 212, 191, 0.2), transparent 55%)",
        }}
        aria-hidden="true"
      />
      <Card className="relative z-10 mx-auto w-full max-w-lg border-border bg-card/85 backdrop-blur-xl dark:border-primary/25 dark:bg-primary/5">
        <CardHeader className="space-y-2">
          <CardTitle className="text-2xl">Create your first workspace</CardTitle>
          <CardDescription>
            Canvas needs a workspace before it can open. Create one to continue
            with your account.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="workspace-name">Workspace name</Label>
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
              Used in links to your workspace. You can keep the suggested value
              or choose a short name.
            </p>
          </div>
          <Button onClick={handleCreateWorkspace} disabled={isCreating}>
            {isCreating ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : null}
            {isCreating ? "Creating…" : "Create workspace"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
