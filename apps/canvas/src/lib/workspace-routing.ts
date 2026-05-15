const RESERVED_PATH_SEGMENTS = new Set([
  "auth",
  "chat",
  "login",
  "profile",
  "settings",
  "workflow-remediations",
]);

const trimPathSegment = (value: string | null | undefined): string | null => {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
};

export const getWorkspaceGalleryPath = (
  workspaceSlug: string | null | undefined,
): string => {
  const slug = trimPathSegment(workspaceSlug);
  return slug ? `/${slug}` : "/";
};

export const getWorkspacePathWithSlug = (
  pathname: string,
  workspaceSlug: string,
): string => {
  const slug = trimPathSegment(workspaceSlug);
  if (!slug) {
    return getWorkspaceGalleryPath(slug);
  }

  const pathSegments = pathname.split("/").filter(Boolean);
  if (pathSegments.length === 0) {
    return getWorkspaceGalleryPath(slug);
  }

  const [firstSegment, ...rest] = pathSegments;
  if (RESERVED_PATH_SEGMENTS.has(firstSegment.toLowerCase())) {
    return getWorkspaceGalleryPath(slug);
  }

  return `/${[slug, ...rest].join("/")}`;
};

export const getWorkspaceWorkflowPath = (
  workspaceSlug: string | null | undefined,
  workflowRef: string,
): string => {
  const slug = trimPathSegment(workspaceSlug);
  const ref = workflowRef.trim();
  if (!ref) {
    return getWorkspaceGalleryPath(slug);
  }
  return slug ? `/${slug}/${ref}` : `/${ref}`;
};

export const getWorkspaceSlugFromPathname = (
  pathname: string,
): string | null => {
  const firstSegment = pathname.split("/").filter(Boolean)[0] ?? null;
  if (!firstSegment) {
    return null;
  }
  if (RESERVED_PATH_SEGMENTS.has(firstSegment.toLowerCase())) {
    return null;
  }
  return firstSegment;
};
