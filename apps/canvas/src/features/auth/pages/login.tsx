import { useMemo } from "react";
import { useLocation } from "react-router-dom";
import AutoLogin from "@features/auth/components/auto-login";

const parseInviteContext = (search: string) => {
  const params = new URLSearchParams(search);
  const normalize = (v: string | null) => v?.trim() || undefined;
  return {
    invitation: normalize(params.get("invitation")),
    organization: normalize(params.get("organization")),
    organizationName: normalize(params.get("organization_name")),
    loginHint: normalize(params.get("login_hint")),
    screenHint: normalize(params.get("screen_hint")),
  };
};

export default function Login() {
  const location = useLocation();
  const inviteContext = useMemo(
    () => parseInviteContext(location.search),
    [location.search],
  );
  return <AutoLogin {...inviteContext} />;
}
