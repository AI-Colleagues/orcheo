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

// Only allow same-origin relative paths to avoid open-redirect via state/query.
const sanitizeRedirect = (value: unknown): string | undefined => {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  if (!trimmed.startsWith("/") || trimmed.startsWith("//")) {
    return undefined;
  }
  return trimmed;
};

const resolveRedirectTo = (
  state: unknown,
  search: string,
): string | undefined => {
  const fromState =
    state && typeof state === "object"
      ? (state as { from?: unknown }).from
      : undefined;
  const stateRedirect = sanitizeRedirect(fromState);
  if (stateRedirect) {
    return stateRedirect;
  }
  const params = new URLSearchParams(search);
  return sanitizeRedirect(params.get("redirect") ?? params.get("from"));
};

export default function Login() {
  const location = useLocation();
  const inviteContext = useMemo(
    () => parseInviteContext(location.search),
    [location.search],
  );
  const redirectTo = useMemo(
    () => resolveRedirectTo(location.state, location.search),
    [location.state, location.search],
  );
  return <AutoLogin {...inviteContext} redirectTo={redirectTo} />;
}
