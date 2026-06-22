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
  listWorkspaceInvitations,
  createWorkspaceInvitation,
  revokeWorkspaceInvitation,
  updateWorkspaceMemberRole,
  removeWorkspaceMember,
  type WorkspaceMember,
  type WorkspaceInvitation,
} from "@/lib/api";
import { getSelectedWorkspaceSlug } from "@/lib/workspace-session";

type MemberRole = "owner" | "admin" | "editor" | "viewer";

const ROLE_OPTIONS: MemberRole[] = ["owner", "admin", "editor", "viewer"];
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const EMAIL_VALIDATION_MESSAGE = "Enter a valid email address.";

const isValidEmail = (email: string): boolean =>
  EMAIL_PATTERN.test(email.trim());

export default function WorkspaceMembers() {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [canManage, setCanManage] = useState(false);
  const [isForbidden, setIsForbidden] = useState(false);

  const [newEmail, setNewEmail] = useState("");
  const [newEmailError, setNewEmailError] = useState<string | null>(null);
  const [newRole, setNewRole] = useState<MemberRole>("editor");
  const [isAdding, setIsAdding] = useState(false);
  const [removingUserId, setRemovingUserId] = useState<string | null>(null);
  const [updatingUserId, setUpdatingUserId] = useState<string | null>(null);
  const [revokingInvitationId, setRevokingInvitationId] = useState<
    string | null
  >(null);

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
        const [memberList, invitationList] = await Promise.all([
          listWorkspaceMembers(slug),
          listWorkspaceInvitations(slug),
        ]);
        if (!active) {
          return;
        }
        setMembers(memberList);
        setInvitations(invitationList);
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

  const handleInvite = async () => {
    const normalizedEmail = newEmail.trim();
    if (!slug || !normalizedEmail) {
      return;
    }
    if (!isValidEmail(normalizedEmail)) {
      setNewEmailError(EMAIL_VALIDATION_MESSAGE);
      return;
    }
    setIsAdding(true);
    try {
      const invitation = await createWorkspaceInvitation(slug, {
        email: normalizedEmail,
        role: newRole,
      });
      setInvitations((prev) => [invitation, ...prev]);
      setNewEmail("");
      setNewEmailError(null);
      setNewRole("editor");
      toast({
        title: "Invitation sent",
        description: `An invite was emailed to ${invitation.email}.`,
      });
    } catch (err) {
      toast({
        title: "Failed to send invitation",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setIsAdding(false);
    }
  };

  const handleRevokeInvitation = async (invitationId: string) => {
    if (!slug) {
      return;
    }
    setRevokingInvitationId(invitationId);
    try {
      const revoked = await revokeWorkspaceInvitation(slug, invitationId);
      setInvitations((prev) =>
        prev.map((i) => (i.id === invitationId ? revoked : i)),
      );
      toast({ title: "Invitation revoked" });
    } catch (err) {
      toast({
        title: "Failed to revoke invitation",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setRevokingInvitationId(null);
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
          <h3 className="mb-1 text-lg font-medium">Invite Member</h3>
          <p className="mb-4 text-sm text-muted-foreground">
            We&apos;ll email an invitation link. The member joins once they
            accept while signed in with a verified email that matches.
          </p>
          <div className="flex items-end gap-3">
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="new-email">Email</Label>
              <Input
                id="new-email"
                type="email"
                placeholder="person@example.com"
                value={newEmail}
                onChange={(e) => {
                  setNewEmail(e.target.value);
                  setNewEmailError(null);
                }}
                disabled={isAdding}
                aria-invalid={newEmailError ? true : undefined}
                aria-describedby={newEmailError ? "new-email-error" : undefined}
              />
              {newEmailError && (
                <p id="new-email-error" className="text-xs text-destructive">
                  {newEmailError}
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
                onClick={() => void handleInvite()}
                disabled={isAdding || !newEmail.trim()}
              >
                {isAdding ? "Sending..." : "Send invite"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {canManage &&
        invitations.some((i) => i.status === "pending") && (
          <div className="rounded-lg border">
            <div className="border-b px-4 py-3">
              <h3 className="text-sm font-medium">Pending Invitations</h3>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Expires</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invitations
                  .filter((i) => i.status === "pending")
                  .map((invitation) => {
                    const isRevoking =
                      revokingInvitationId === invitation.id;
                    const isExpired =
                      new Date(invitation.expires_at) < new Date();
                    return (
                      <TableRow
                        key={invitation.id}
                        className={isExpired ? "opacity-60" : undefined}
                      >
                        <TableCell className="text-sm">
                          {invitation.email}
                        </TableCell>
                        <TableCell className="capitalize">
                          {invitation.role}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {isExpired ? (
                            <span className="text-destructive">Expired</span>
                          ) : (
                            new Date(
                              invitation.expires_at,
                            ).toLocaleDateString()
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-destructive hover:text-destructive"
                            onClick={() =>
                              void handleRevokeInvitation(invitation.id)
                            }
                            disabled={isRevoking}
                          >
                            {isRevoking ? "Revoking..." : "Revoke"}
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
              </TableBody>
            </Table>
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
                <TableHead>Email / ID</TableHead>
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
                      {member.user_name ?? member.email ?? "—"}
                    </TableCell>
                    <TableCell className="text-sm">
                      {member.email ? (
                        <div className="flex flex-col">
                          <span>{member.email}</span>
                          <span className="font-mono text-xs text-muted-foreground">
                            {member.user_id}
                          </span>
                        </div>
                      ) : (
                        <span className="font-mono">{member.user_id}</span>
                      )}
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
