import {
  getAccessToken,
  getAuthTokens,
  getDevAuthSessionHeaderValue,
} from "@features/auth/lib/auth-session";
import { refreshSession } from "@features/auth/lib/auth-api";
import { getWorkspaceSelectionHeaders } from "./workspace-session";

const buildAuthHeaders = (init: RequestInit): Headers => {
  const headers = new Headers(init.headers ?? {});
  const token = getAccessToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const devSession = getDevAuthSessionHeaderValue();
  if (devSession && !headers.has("X-Orcheo-Dev-Session")) {
    headers.set("X-Orcheo-Dev-Session", devSession);
  }
  return headers;
};

const attachWorkspaceHeaders = (headers: Headers): void => {
  for (const [name, value] of Object.entries(getWorkspaceSelectionHeaders())) {
    if (!headers.has(name)) {
      headers.set(name, value);
    }
  }
};

const fetchWithHeaders = (
  input: RequestInfo | URL,
  init: RequestInit,
  headers: Headers,
): Promise<Response> =>
  globalThis.fetch(input, {
    ...init,
    credentials: init.credentials ?? "include",
    headers,
  });

export const authFetch = async (
  input: RequestInfo | URL,
  init: RequestInit = {},
  options: { includeWorkspaceHeaders?: boolean } = {},
): Promise<Response> => {
  const shouldAttachAuth = !new Headers(init.headers ?? {}).has(
    "Authorization",
  );
  if (shouldAttachAuth && !getAccessToken() && getAuthTokens()?.refreshToken) {
    await refreshSession();
  }

  const headers = buildAuthHeaders(init);
  if (options.includeWorkspaceHeaders ?? true) {
    attachWorkspaceHeaders(headers);
  }
  const response = await fetchWithHeaders(input, init, headers);
  if (
    response.status !== 401 ||
    !shouldAttachAuth ||
    !getAuthTokens()?.refreshToken ||
    !(await refreshSession())
  ) {
    return response;
  }

  const retryHeaders = buildAuthHeaders(init);
  if (options.includeWorkspaceHeaders ?? true) {
    attachWorkspaceHeaders(retryHeaders);
  }
  return fetchWithHeaders(input, init, retryHeaders);
};
