const WORKSPACE_SLUG_KEY = "orcheo_canvas_workspace_slug";
const WORKSPACE_HEADER = "X-Orcheo-Workspace";

const safeLocalStorageGet = (key: string): string | null => {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};

const safeLocalStorageSet = (key: string, value: string | null): void => {
  if (typeof window === "undefined") {
    return;
  }

  try {
    if (value === null) {
      window.localStorage.removeItem(key);
      return;
    }
    window.localStorage.setItem(key, value);
  } catch {
    return;
  }
};

const normalizeSlug = (value: string): string => value.trim();

export const getWorkspaceHeaderName = (): string => WORKSPACE_HEADER;

export const getSelectedWorkspaceSlug = (): string | null => {
  const slug = safeLocalStorageGet(WORKSPACE_SLUG_KEY);
  if (!slug) {
    return null;
  }
  const normalized = normalizeSlug(slug);
  return normalized || null;
};

export const setSelectedWorkspaceSlug = (slug: string | null): void => {
  if (slug === null) {
    safeLocalStorageSet(WORKSPACE_SLUG_KEY, null);
  } else {
    safeLocalStorageSet(WORKSPACE_SLUG_KEY, normalizeSlug(slug));
  }
};

export const clearSelectedWorkspaceSlug = (): void => {
  setSelectedWorkspaceSlug(null);
};

export const getWorkspaceSelectionHeaders = (): Record<string, string> => {
  const slug = getSelectedWorkspaceSlug();
  if (!slug) {
    return {};
  }

  return {
    [getWorkspaceHeaderName()]: slug,
  };
};
