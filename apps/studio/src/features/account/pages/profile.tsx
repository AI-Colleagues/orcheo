import { useEffect, useMemo } from "react";
import { usePageContext } from "@/hooks/use-page-context";
import { getAuthenticatedUserProfile } from "@features/auth/lib/auth-session";
import type { ProfileUser } from "./profile/types";
import { ProfileGeneralTab } from "./profile/components/profile-general-tab";

const LOCAL_DEV_PROFILE: ProfileUser = {
  name: "Local Developer",
  email: "local@orcheo.dev",
  avatar: "https://avatar.vercel.sh/orcheo-local",
  role: "Local development",
  joinDate: undefined,
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
    };
  }, [authUser]);

  return (
    <main className="h-full min-h-0 overflow-auto">
      <div className="mx-auto flex w-full max-w-7xl flex-col space-y-4 p-8 pt-6">
        <div className="flex items-center justify-between space-y-2">
          <h2 className="text-3xl font-bold tracking-tight">Profile</h2>
        </div>
        <ProfileGeneralTab user={user} />
      </div>
    </main>
  );
}
