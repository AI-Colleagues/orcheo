/**
 * Theme initialization utility
 *
 * This module provides functions to initialize and manage theme settings
 * across the application. It ensures theme preferences persist across
 * page refreshes by reading from localStorage and applying the appropriate
 * CSS classes before the React app renders.
 */

export type Theme = "light" | "dark" | "system";

/**
 * Initialize the theme on app load
 * This should be called before React renders to prevent theme flashing
 */
export function initializeTheme(): void {
  // Get saved theme preference from localStorage
  const savedTheme = localStorage.getItem("theme") as Theme | null;
  const theme = savedTheme || "system";

  // Apply the theme
  applyTheme(theme);

  // Apply other preferences
  const reducedMotion = localStorage.getItem("reducedMotion") === "true";
  const highContrast = localStorage.getItem("highContrast") === "true";
  const accentColor = localStorage.getItem("accentColor") || "blue";

  applyReducedMotion(reducedMotion);
  applyHighContrast(highContrast);
  applyAccentColor(accentColor);
}

/**
 * Apply a theme to the document
 * @param theme - The theme to apply ("light", "dark", or "system")
 */
export function applyTheme(theme: Theme): void {
  if (theme === "system") {
    // Use system preference
    const systemTheme = window.matchMedia("(prefers-color-scheme: dark)")
      .matches
      ? "dark"
      : "light";
    document.documentElement.classList.toggle("dark", systemTheme === "dark");
  } else {
    // Use explicit theme
    document.documentElement.classList.toggle("dark", theme === "dark");
  }
}

/**
 * Apply the reduced motion preference to the document root.
 */
export function applyReducedMotion(enabled: boolean): void {
  document.documentElement.classList.toggle("reduce-motion", enabled);
}

/**
 * Apply the high contrast preference to the document root.
 */
export function applyHighContrast(enabled: boolean): void {
  document.documentElement.classList.toggle("high-contrast", enabled);
}

/**
 * Apply the accent color preference to the document root.
 */
export function applyAccentColor(color: string): void {
  document.documentElement.setAttribute("data-accent", color);
}

/**
 * Listen for system theme changes when using "system" theme
 * @param callback - Function to call when system theme changes
 * @returns Cleanup function to remove the listener
 */
export function listenToSystemTheme(
  callback: (isDark: boolean) => void,
): () => void {
  const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

  const handler = (e: MediaQueryListEvent) => {
    callback(e.matches);
  };

  mediaQuery.addEventListener("change", handler);

  return () => {
    mediaQuery.removeEventListener("change", handler);
  };
}

/**
 * Keep the applied theme in sync with the OS color scheme while the stored
 * preference is "system". Without this, switching the OS between light and
 * dark only takes effect after a full page reload.
 * @returns Cleanup function to remove the listener
 */
export function watchSystemTheme(): () => void {
  return listenToSystemTheme(() => {
    const savedTheme =
      (localStorage.getItem("theme") as Theme | null) ?? "system";

    if (savedTheme === "system") {
      applyTheme("system");
    }
  });
}
