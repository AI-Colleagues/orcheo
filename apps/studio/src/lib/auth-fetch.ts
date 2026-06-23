import {
  getAccessToken,
  getAuthTokens,
  getDevAuthSessionHeaderValue,
} from "@features/auth/lib/auth-session";
import { refreshSession } from "@features/auth/lib/auth-api";
import { getWorkspaceSelectionHeaders } from "./workspace-session";

const buildAuthHeaders = (
  headers: Headers,
  shouldAttachAuth: boolean,
): string | null => {
  const token = getAccessToken();
  if (token && shouldAttachAuth) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const devSession = getDevAuthSessionHeaderValue();
  if (devSession && !headers.has("X-Orcheo-Dev-Session")) {
    headers.set("X-Orcheo-Dev-Session", devSession);
  }
  return shouldAttachAuth ? token : null;
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

const buildRequestHeaders = (
  init: RequestInit,
  shouldAttachAuth: boolean,
  includeWorkspaceHeaders: boolean,
  headers = new Headers(init.headers ?? {}),
): { headers: Headers; accessToken: string | null } => {
  const accessToken = buildAuthHeaders(headers, shouldAttachAuth);
  if (includeWorkspaceHeaders) {
    attachWorkspaceHeaders(headers);
  }
  return { headers, accessToken };
};

export const authFetch = async (
  input: RequestInfo | URL,
  init: RequestInit = {},
  options: { includeWorkspaceHeaders?: boolean } = {},
): Promise<Response> => {
  const originalHeaders = new Headers(init.headers ?? {});
  const shouldAttachAuth = !originalHeaders.has("Authorization");
  // getAccessToken includes the shared 60-second expiry skew, so nearly expired
  // tokens are treated as missing and refreshed before protected requests.
  if (shouldAttachAuth && !getAccessToken() && getAuthTokens()?.refreshToken) {
    await refreshSession();
  }

  const includeWorkspaceHeaders = options.includeWorkspaceHeaders ?? true;
  const { headers, accessToken: requestAccessToken } = buildRequestHeaders(
    init,
    shouldAttachAuth,
    includeWorkspaceHeaders,
    originalHeaders,
  );
  const response = await fetchWithHeaders(input, init, headers);
  if (response.status !== 401 || !shouldAttachAuth) {
    return response;
  }

  const currentAccessToken = getAccessToken();
  if (currentAccessToken && currentAccessToken !== requestAccessToken) {
    const { headers: retryHeaders } = buildRequestHeaders(
      init,
      shouldAttachAuth,
      includeWorkspaceHeaders,
    );
    return fetchWithHeaders(input, init, retryHeaders);
  }

  if (!getAuthTokens()?.refreshToken || !(await refreshSession())) {
    return response;
  }

  const { headers: retryHeaders } = buildRequestHeaders(
    init,
    shouldAttachAuth,
    includeWorkspaceHeaders,
  );
  return fetchWithHeaders(input, init, retryHeaders);
};
