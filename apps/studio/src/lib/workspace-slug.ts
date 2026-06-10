const WORKSPACE_SLUG_PATTERN = /[^a-z0-9_-]+/g;

export function slugifyWorkspace(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(WORKSPACE_SLUG_PATTERN, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}
