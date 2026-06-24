import type { Workflow } from "@features/workflow/data/workflow-data";
import type { CandidateBadgeDefinition } from "@features/workflow/data/templates/candidate-badges";
import type { CandidateUpdateNote } from "@features/workflow/lib/workflow-storage.types";

interface SemVer {
  major: number;
  minor: number;
  patch: number;
}

export interface CandidateUpdateAvailability {
  candidateId: string;
  candidateName: string;
  currentVersion: string;
  latestVersion: string;
  latestSummary?: string;
  firstMigration?: string;
  crossedUpdates: CandidateUpdateNote[];
  isMajor: boolean;
}

const SEMVER_PATTERN = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

export const parseSemVer = (value: string): SemVer | null => {
  const match = SEMVER_PATTERN.exec(value);
  if (!match) {
    return null;
  }

  return {
    major: Number.parseInt(match[1] ?? "0", 10),
    minor: Number.parseInt(match[2] ?? "0", 10),
    patch: Number.parseInt(match[3] ?? "0", 10),
  };
};

export const compareSemVer = (left: string, right: string): number | null => {
  const leftVersion = parseSemVer(left);
  const rightVersion = parseSemVer(right);
  if (!leftVersion || !rightVersion) {
    return null;
  }

  for (const key of ["major", "minor", "patch"] as const) {
    const delta = leftVersion[key] - rightVersion[key];
    if (delta !== 0) {
      return delta > 0 ? 1 : -1;
    }
  }

  return 0;
};

export const getCandidateUpdateAvailability = (
  workflow: Workflow,
  candidate: CandidateBadgeDefinition | undefined,
): CandidateUpdateAvailability | undefined => {
  if (!candidate?.version) {
    return undefined;
  }

  const candidateSource = workflow.versions?.at(-1)?.candidateSource;
  if (candidateSource?.source !== "candidate-onboard") {
    return undefined;
  }

  const currentVersion = candidateSource.candidateVersion;
  if (!currentVersion) {
    return undefined;
  }

  const latestVersion = candidate.version;
  if (compareSemVer(latestVersion, currentVersion) !== 1) {
    return undefined;
  }

  const currentParsed = parseSemVer(currentVersion);
  const latestParsed = parseSemVer(latestVersion);
  if (!currentParsed || !latestParsed) {
    return undefined;
  }

  const crossedUpdates = (candidate.updates ?? []).filter((note) => {
    const newerThanCurrent = compareSemVer(note.version, currentVersion);
    const notNewerThanLatest = compareSemVer(note.version, latestVersion);
    return (
      newerThanCurrent === 1 &&
      notNewerThanLatest !== null &&
      notNewerThanLatest <= 0
    );
  });

  const firstMigration = crossedUpdates.find(
    (note) => typeof note.migration === "string" && note.migration.trim(),
  )?.migration;

  return {
    candidateId: candidate.candidateId,
    candidateName: candidate.name,
    currentVersion,
    latestVersion,
    latestSummary: crossedUpdates[0]?.summary,
    firstMigration,
    crossedUpdates,
    isMajor: latestParsed.major > currentParsed.major,
  };
};
