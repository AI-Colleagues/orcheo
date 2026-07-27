import { describe, expect, it } from "vitest";
import {
  isColleaguesSectionActive,
  isPathWithinSection,
} from "./route-matchers";

describe("app sidebar route matching", () => {
  it("matches only complete Apps path segments", () => {
    expect(isPathWithinSection("/acme/apps", "/acme/apps")).toBe(true);
    expect(isPathWithinSection("/acme/apps/app-1", "/acme/apps")).toBe(true);
    expect(isPathWithinSection("/acme/apps-onboarding", "/acme/apps")).toBe(
      false,
    );
  });

  it("keeps workflow slugs that start with reserved words in colleagues", () => {
    expect(isColleaguesSectionActive("/acme/apps-onboarding", "acme")).toBe(
      true,
    );
    expect(isColleaguesSectionActive("/acme/workspace-audit", "acme")).toBe(
      true,
    );
    expect(isColleaguesSectionActive("/acme/apps/app-1", "acme")).toBe(false);
    expect(isColleaguesSectionActive("/acme/workspace", "acme")).toBe(false);
  });
});
