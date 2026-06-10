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
import { toast } from "@/hooks/use-toast";
import { getAuthenticatedUserProfile } from "@features/auth/lib/auth-session";
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
const USER_ID_PATTERN = /^[A-Za-z0-9._:@+-]{3,128}$/;
const USER_ID_VALIDATION_MESSAGE =
  "Enter a valid user ID using letters, numbers, dots, underscores, colons, at signs, plus signs, or hyphens.";

const isValidUserId = (userId: string): boolean =>
  USER_ID_PATTERN.test(userId.trim());

export default function WorkspaceMembers() {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [canManage, setCanManage] = useState(false);
  const [isForbidden, setIsForbidden] = useState(false);

  const [newUserId, setNewUserId] = useState("");
  const [newUserIdError, setNewUserIdError] = useState<string | null>(null);
  const [newRole, setNewRole] = useState<MemberRole>("editor");
  const [isAdding, setIsAdding] = useState(false);
  const [removingUserId, setRemovingUserId] = useState<string | null>(null);
  const [updatingUserId, setUpdatingUserId] = useState<string | null>(null);

  const slug = getSelectedWorkspaceSlug();
  const currentUserId = getAuthenticatedUserProfile()?.subject ?? null;

  useEffect(() => {
    if (!slug) {
      setIsLoading(false);
      return;
    }

    let active = true;

    const loadData = async () => {
      try {
        const workspace = await getActiveWorkspace();
        if (!active) {
          return;
        }
        const allowed =
          workspace.role === "admin" || workspace.role === "owner";
        setCanManage(allowed);
        if (!allowed) {
          setIsForbidden(true);
          return;
        }
        const memberList = await listWorkspaceMembers(slug);
        if (!active) {
          return;
        }
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
    const normalizedUserId = newUserId.trim();
    if (!slug || !normalizedUserId) {
      return;
    }
    if (!isValidUserId(normalizedUserId)) {
      setNewUserIdError(USER_ID_VALIDATION_MESSAGE);
      return;
    }
    setIsAdding(true);
    try {
      const member = await addWorkspaceMember(slug, {
        user_id: normalizedUserId,
        role: newRole,
      });
      setMembers((prev) => [...prev, member]);
      setNewUserId("");
      setNewUserIdError(null);
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
    <div className="flex flex-col space-y-6">
      {isForbidden && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          You do not have permission to view workspace members. Ask a workspace
          admin or owner to grant you access.
        </div>
      )}

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
                onChange={(e) => {
                  setNewUserId(e.target.value);
                  setNewUserIdError(null);
                }}
                disabled={isAdding}
                aria-invalid={newUserIdError ? true : undefined}
                aria-describedby={
                  newUserIdError ? "new-user-id-error" : undefined
                }
              />
              {newUserIdError && (
                <p id="new-user-id-error" className="text-xs text-destructive">
                  {newUserIdError}
                </p>
              )}
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
            <div className="space-y-1.5">
              <Label className="invisible" aria-hidden>
                Action
              </Label>
              <Button
                onClick={() => void handleAddMember()}
                disabled={isAdding || !newUserId.trim()}
              >
                {isAdding ? "Adding..." : "Add"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {isForbidden ? null : isLoading ? (
        <p className="text-sm text-muted-foreground">Loading members...</p>
      ) : members.length === 0 ? (
        <p className="text-sm text-muted-foreground">No members found.</p>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>User ID</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Joined</TableHead>
                {canManage && (
                  <TableHead className="text-right">Actions</TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.map((member) => {
                const isSelf = member.user_id === currentUserId;
                const isUpdating = updatingUserId === member.user_id;
                const isRemoving = removingUserId === member.user_id;

                return (
                  <TableRow key={member.id}>
                    <TableCell className="text-sm">
                      {member.user_name ?? "—"}
                    </TableCell>
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
  );
}
