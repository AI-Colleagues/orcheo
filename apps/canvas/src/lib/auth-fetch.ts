import {
  getAccessToken,
  getDevAuthSessionHeaderValue,
} from "@features/auth/lib/auth-session";
import { getWorkspaceSelectionHeaders } from "./workspace-session";

export const authFetch = async (
  input: RequestInfo | URL,
  init: RequestInit = {},
  options: { includeWorkspaceHeaders?: boolean } = {},
): Promise<Response> => {
  const headers = new Headers(init.headers ?? {});
  const token = getAccessToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const devSession = getDevAuthSessionHeaderValue();
  if (devSession && !headers.has("X-Orcheo-Dev-Session")) {
    headers.set("X-Orcheo-Dev-Session", devSession);
  }
  if (options.includeWorkspaceHeaders ?? true) {
    for (const [name, value] of Object.entries(getWorkspaceSelectionHeaders())) {
      if (!headers.has(name)) {
        headers.set(name, value);
      }
    }
  }
  return globalThis.fetch(input, {
    ...init,
    credentials: init.credentials ?? "include",
    headers,
  });
};
