import React, { useCallback, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Avatar, AvatarFallback, AvatarImage } from "@/design-system/ui/avatar";
import { Button } from "@/design-system/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/design-system/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/design-system/ui/dialog";
import { HelpCircle, Key, LogOut, Settings, User } from "lucide-react";
import {
  clearAuthSession,
  getAuthenticatedUserProfile,
} from "@features/auth/lib/auth-session";
import CredentialsVault from "@features/workflow/components/dialogs/credentials-vault";
import { usePageContext } from "@/hooks/use-page-context";
import type {
  Credential,
  CredentialInput,
  CredentialUpdateInput,
} from "@features/workflow/types/credential-vault";

interface AccountMenuProps {
  credentials: Credential[];
  isCredentialsLoading: boolean;
  onAddCredential?: (credential: CredentialInput) => Promise<void> | void;
  onUpdateCredential?: (
    id: string,
    updates: CredentialUpdateInput,
  ) => Promise<void> | void;
  onDeleteCredential?: (id: string) => Promise<void> | void;
  onRevealCredentialSecret?: (id: string) => Promise<string | null>;
}

export default function AccountMenu({
  credentials,
  isCredentialsLoading,
  onAddCredential,
  onUpdateCredential,
  onDeleteCredential,
  onRevealCredentialSecret,
}: AccountMenuProps) {
  const [isVaultOpen, setIsVaultOpen] = useState(false);
  const { setVaultOpen } = usePageContext();
  const navigate = useNavigate();
  const authUser = getAuthenticatedUserProfile();
  const accountLabel = authUser?.name ?? "Account";
  const accountInitials = accountLabel
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const handleVaultOpenChange = useCallback(
    (open: boolean) => {
      setIsVaultOpen(open);
      setVaultOpen(open);
    },
    [setVaultOpen],
  );

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            className="h-10 w-10 rounded-full border-2 border-border p-0"
            aria-label={
              authUser ? `Account menu for ${authUser.name}` : "Account menu"
            }
          >
            {authUser ? (
              <Avatar className="h-9 w-9">
                {authUser.avatar ? (
                  <AvatarImage src={authUser.avatar} alt={authUser.name} />
                ) : null}
                <AvatarFallback>
                  {accountInitials || <User className="h-5 w-5" />}
                </AvatarFallback>
              </Avatar>
            ) : (
              <User className="h-5 w-5" />
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuLabel>My Account</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem>
            <Link to="/profile" className="flex w-full items-center">
              <User className="mr-2 h-4 w-4" />
              <span>Profile</span>
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem>
            <Link to="/settings" className="flex w-full items-center">
              <Settings className="mr-2 h-4 w-4" />
              <span>Settings</span>
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={(event) => {
              event.preventDefault();
              handleVaultOpenChange(true);
            }}
            className="cursor-pointer"
          >
            <div className="flex w-full items-center">
              <Key className="mr-2 h-4 w-4" />
              <span>Credential Vault</span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuItem>
            <Link to="/help-support" className="flex w-full items-center">
              <HelpCircle className="mr-2 h-4 w-4" />
              <span>Help & Support</span>
            </Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => {
              clearAuthSession();
              navigate("/login", { replace: true });
            }}
            className="cursor-pointer"
          >
            <div className="flex w-full items-center">
              <LogOut className="mr-2 h-4 w-4" />
              <span>Log out</span>
            </div>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={isVaultOpen} onOpenChange={handleVaultOpenChange}>
        <DialogContent className="max-h-[85vh] max-w-[67.2rem] overflow-hidden">
          <DialogTitle className="sr-only">Credential Vault</DialogTitle>
          <DialogDescription className="sr-only">
            Manage, search, add, and remove credentials.
          </DialogDescription>
          <CredentialsVault
            credentials={credentials}
            isLoading={isCredentialsLoading}
            onAddCredential={onAddCredential}
            onUpdateCredential={onUpdateCredential}
            onDeleteCredential={onDeleteCredential}
            onRevealCredentialSecret={onRevealCredentialSecret}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
