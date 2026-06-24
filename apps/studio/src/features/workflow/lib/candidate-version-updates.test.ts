import { describe, expect, it } from "vitest";
import {
  compareStrictSemver,
  resolveCandidateUpdateAvailability,
} from "./candidate-version-updates";

describe("candidate version updates", () => {
  it("compares strict semantic versions by segment", () => {
    expect(compareStrictSemver("1.10.0", "1.2.9")).toBe(1);
    expect(compareStrictSemver("1.2.0", "1.2.0")).toBe(0);
    expect(compareStrictSemver("1.2.0", "2.0.0")).toBe(-1);
    expect(Number.isNaN(compareStrictSemver("v1.2.0", "1.2.0"))).toBe(true);
  });

  it("resolves crossed update notes and major-update state", () => {
    const availability = resolveCandidateUpdateAvailability(
      {
        candidateId: "insight-analyst",
        candidateHandle: "insight-analyst",
        candidateVersion: "1.2.0",
      },
      {
        candidateId: "insight-analyst",
        handle: "insight-analyst",
        version: "2.0.0",
        updates: [
          {
            version: "2.0.0",
            summary: "Changes prompt contracts.",
            migration: "Review custom prompts.",
          },
          { version: "1.3.0", summary: "Adds source checks." },
          { version: "1.1.0", summary: "Old note." },
        ],
      },
    );

    expect(availability).toMatchObject({
      candidateId: "insight-analyst",
      currentVersion: "1.2.0",
      latestVersion: "2.0.0",
      latestSummary: "Changes prompt contracts.",
      firstMigration: "Review custom prompts.",
      isMajorUpdate: true,
    });
    expect(availability?.crossedUpdates.map((note) => note.version)).toEqual([
      "2.0.0",
      "1.3.0",
    ]);
  });

  it("returns undefined when versions are missing, invalid, or not newer", () => {
    expect(
      resolveCandidateUpdateAvailability(
        { candidateId: "x", candidateVersion: "1.0.0" },
        { candidateId: "x", handle: "x", version: "1.0.0" },
      ),
    ).toBeUndefined();
    expect(
      resolveCandidateUpdateAvailability(
        { candidateId: "x", candidateVersion: "1.0" },
        { candidateId: "x", handle: "x", version: "1.1.0" },
      ),
    ).toBeUndefined();
    expect(
      resolveCandidateUpdateAvailability(
        { candidateId: "x", candidateVersion: "1.0.0" },
        { candidateId: "y", handle: "y", version: "1.1.0" },
      ),
    ).toBeUndefined();
  });
});
