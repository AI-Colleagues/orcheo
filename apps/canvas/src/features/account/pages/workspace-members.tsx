import { useEffect, useState } from "react";
import { Button } from "@/design-system/ui/button";
import { Input } from "@/design-system/ui/input";
import { Label } from "@/design-system/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/design-system/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/design-system/ui/table";
import useCredentialVault from "@/hooks/use-credential-vault";
import { usePageContext } from "@/hooks/use-page-context";
import { toast } from "@/hooks/use-toast";
import TopNavigation from "@features/shared/components/top-navigation";
import {
  getActiveWorkspace,
  listWorkspaceMembers,
  addWorkspaceMember,
  updateWorkspaceMemberRole,
  removeWorkspaceMember,
  type WorkspaceMember,
} from "@/lib/api";
import { getSelectedWorkspaceSlug } from "@/lib/workspace-session";

type MemberRole = "owner" | "admin" | "editor" | "viewer";

const ROLE_OPTIONS: MemberRole[] = ["owner", "admin", "editor", "viewer"];

export default function WorkspaceMembers() {
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

  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [canManage, setCanManage] = useState(false);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);

  const [newUserId, setNewUserId] = useState("");
  const [newRole, setNewRole] = useState<MemberRole>("editor");
  const [isAdding, setIsAdding] = useState(false);
  const [removingUserId, setRemovingUserId] = useState<string | null>(null);
  const [updatingUserId, setUpdatingUserId] = useState<string | null>(null);

  const slug = getSelectedWorkspaceSlug();

  useEffect(() => {
    if (!slug) {
      return;
    }

    let active = true;

    const loadData = async () => {
      try {
        const [workspace, memberList] = await Promise.all([
          getActiveWorkspace(),
          listWorkspaceMembers(slug),
        ]);
        if (!active) {
          return;
        }
        setCanManage(
          workspace.role === "admin" || workspace.role === "owner",
        );
        setCurrentUserId(workspace.workspace_id ?? null);
        setMembers(memberList);
      } catch {
        if (active) {
          toast({
            title: "Failed to load workspace members",
            variant: "destructive",
          });
        }
      } finally {
        if (active) {
          setIsLoading(false);
        }
      }
    };

    void loadData();

    return () => {
      active = false;
    };
  }, [slug]);

  const handleAddMember = async () => {
    if (!slug || !newUserId.trim()) {
      return;
    }
    setIsAdding(true);
    try {
      const member = await addWorkspaceMember(
        slug,
        { user_id: newUserId.trim(), role: newRole },
      );
      setMembers((prev) => [...prev, member]);
      setNewUserId("");
      setNewRole("editor");
      toast({ title: "Member added successfully" });
    } catch (err) {
      toast({
        title: "Failed to add member",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setIsAdding(false);
    }
  };

  const handleRoleChange = async (userId: string, role: MemberRole) => {
    if (!slug) {
      return;
    }
    setUpdatingUserId(userId);
    try {
      const updated = await updateWorkspaceMemberRole(slug, userId, role);
      setMembers((prev) =>
        prev.map((m) => (m.user_id === userId ? updated : m)),
      );
      toast({ title: "Role updated" });
    } catch (err) {
      toast({
        title: "Failed to update role",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setUpdatingUserId(null);
    }
  };

  const handleRemoveMember = async (userId: string) => {
    if (!slug) {
      return;
    }
    setRemovingUserId(userId);
    try {
      await removeWorkspaceMember(slug, userId);
      setMembers((prev) => prev.filter((m) => m.user_id !== userId));
      toast({ title: "Member removed" });
    } catch (err) {
      toast({
        title: "Failed to remove member",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setRemovingUserId(null);
    }
  };

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
          <div className="flex items-center justify-between">
            <h2 className="text-3xl font-bold tracking-tight">
              Workspace Members
            </h2>
          </div>

          {canManage && (
            <div className="rounded-lg border p-4">
              <h3 className="mb-4 text-lg font-medium">Add Member</h3>
              <div className="flex items-end gap-3">
                <div className="flex-1 space-y-1.5">
                  <Label htmlFor="new-user-id">User ID</Label>
                  <Input
                    id="new-user-id"
                    placeholder="Enter user ID"
                    value={newUserId}
                    onChange={(e) => setNewUserId(e.target.value)}
                    disabled={isAdding}
                  />
                </div>
                <div className="w-36 space-y-1.5">
                  <Label htmlFor="new-role">Role</Label>
                  <Select
                    value={newRole}
                    onValueChange={(v) => setNewRole(v as MemberRole)}
                    disabled={isAdding}
                  >
                    <SelectTrigger id="new-role">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ROLE_OPTIONS.map((role) => (
                        <SelectItem key={role} value={role}>
                          {role}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <Button
                  onClick={() => void handleAddMember()}
                  disabled={isAdding || !newUserId.trim()}
                >
                  {isAdding ? "Adding..." : "Add"}
                </Button>
              </div>
            </div>
          )}

          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading members...</p>
          ) : members.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No members found.
            </p>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>User ID</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Joined</TableHead>
                    {canManage && <TableHead className="text-right">Actions</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {members.map((member) => {
                    const isSelf = member.user_id === currentUserId;
                    const isUpdating = updatingUserId === member.user_id;
                    const isRemoving = removingUserId === member.user_id;

                    return (
                      <TableRow key={member.id}>
                        <TableCell className="font-mono text-sm">
                          {member.user_id}
                        </TableCell>
                        <TableCell>
                          {canManage && !isSelf ? (
                            <Select
                              value={member.role}
                              onValueChange={(v) =>
                                void handleRoleChange(
                                  member.user_id,
                                  v as MemberRole,
                                )
                              }
                              disabled={isUpdating}
                            >
                              <SelectTrigger className="w-32">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {ROLE_OPTIONS.map((role) => (
                                  <SelectItem key={role} value={role}>
                                    {role}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          ) : (
                            <span className="capitalize">{member.role}</span>
                          )}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {new Date(member.created_at).toLocaleDateString()}
                        </TableCell>
                        {canManage && (
                          <TableCell className="text-right">
                            {!isSelf && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="text-destructive hover:text-destructive"
                                onClick={() =>
                                  void handleRemoveMember(member.user_id)
                                }
                                disabled={isRemoving}
                              >
                                {isRemoving ? "Removing..." : "Remove"}
                              </Button>
                            )}
                          </TableCell>
                        )}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
