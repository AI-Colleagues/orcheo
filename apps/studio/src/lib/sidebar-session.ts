const SIDEBAR_COLLAPSED_KEY = "orcheo_studio_sidebar_collapsed";

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

const safeLocalStorageSet = (key: string, value: string): void => {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(key, value);
  } catch {
    return;
  }
};

export const getSidebarCollapsed = (): boolean =>
  safeLocalStorageGet(SIDEBAR_COLLAPSED_KEY) === "true";

export const setSidebarCollapsed = (collapsed: boolean): void => {
  safeLocalStorageSet(SIDEBAR_COLLAPSED_KEY, collapsed ? "true" : "false");
};
