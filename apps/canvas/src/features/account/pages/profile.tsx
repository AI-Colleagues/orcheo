import { useEffect, useMemo } from "react";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/design-system/ui/tabs";
import TopNavigation from "@features/shared/components/top-navigation";
import useCredentialVault from "@/hooks/use-credential-vault";
import { usePageContext } from "@/hooks/use-page-context";
import { getAuthenticatedUserProfile } from "@features/auth/lib/auth-session";
import type { ProfileUser } from "./profile/types";
import { ProfileGeneralTab } from "./profile/components/profile-general-tab";
import { ProfileSecurityTab } from "./profile/components/profile-security-tab";
import { ProfileApiKeysTab } from "./profile/components/profile-api-keys-tab";

const LOCAL_DEV_PROFILE: ProfileUser = {
  name: "Local Developer",
  email: "local@orcheo.dev",
  avatar: "https://avatar.vercel.sh/orcheo-local",
  role: "Local development",
  joinDate: "Local development session",
  twoFactorEnabled: false,
};

export default function Profile() {
  const { setPageContext } = usePageContext();
  useEffect(() => {
    setPageContext({ page: "profile" });
  }, [setPageContext]);
  const authUser = useMemo(() => getAuthenticatedUserProfile(), []);
  const user = useMemo<ProfileUser>(() => {
    if (!authUser) {
      return LOCAL_DEV_PROFILE;
    }

    const avatarSeed = authUser.subject ?? authUser.email ?? authUser.name;
    return {
      name: authUser.name,
      email: authUser.email ?? "",
      avatar:
        authUser.avatar ??
        `https://avatar.vercel.sh/${encodeURIComponent(avatarSeed)}`,
      role: authUser.role ?? "Member",
      twoFactorEnabled: false,
    };
  }, [authUser]);

  const actorName = authUser?.subject ?? authUser?.email ?? user.name;

  const {
    credentials,
    isLoading: isCredentialsLoading,
    onAddCredential,
    onUpdateCredential,
    onDeleteCredential,
    onRevealCredentialSecret,
  } = useCredentialVault({ actorName });

  return (
    <div className="flex min-h-screen flex-col">
      <TopNavigation
        credentials={credentials}
        isCredentialsLoading={isCredentialsLoading}
        onAddCredential={onAddCredential}
        onUpdateCredential={onUpdateCredential}
        onDeleteCredential={onDeleteCredential}
        onRevealCredentialSecret={onRevealCredentialSecret}
      />

      <div className="flex-1 space-y-4 p-8 pt-6 mx-auto w-full max-w-7xl">
        <div className="flex items-center justify-between space-y-2">
          <h2 className="text-3xl font-bold tracking-tight">Profile</h2>
        </div>
        <Tabs defaultValue="general" className="space-y-4">
          <TabsList>
            <TabsTrigger value="general">General</TabsTrigger>
            <TabsTrigger value="security">Security</TabsTrigger>
            <TabsTrigger value="api-keys">API Keys</TabsTrigger>
          </TabsList>
          <TabsContent value="general" className="space-y-4">
            <ProfileGeneralTab user={user} />
          </TabsContent>
          <TabsContent value="security" className="space-y-4">
            <ProfileSecurityTab user={user} />
          </TabsContent>
          <TabsContent value="api-keys" className="space-y-4">
            <ProfileApiKeysTab />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
