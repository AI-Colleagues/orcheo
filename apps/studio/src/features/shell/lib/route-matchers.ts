export const isColleaguesSectionActive = (
  pathname: string,
  workspaceSlug: string | null,
): boolean => {
  if (!workspaceSlug) {
    return pathname === "/";
  }
  const galleryPath = `/${workspaceSlug}`;
  if (pathname === galleryPath) {
    return true;
  }
  const prefix = `${galleryPath}/`;
  if (!pathname.startsWith(prefix)) {
    return false;
  }
  const firstSegment = pathname.slice(prefix.length).split("/", 1)[0];
  return firstSegment !== "apps" && firstSegment !== "workspace";
};

export const isPathWithinSection = (
  pathname: string,
  sectionPath: string,
): boolean =>
  pathname === sectionPath || pathname.startsWith(`${sectionPath}/`);
