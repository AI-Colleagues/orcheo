export interface CandidateUpdateNote {
  version: string;
  summary: string;
  migration?: string | null;
}

export interface CandidateReleaseInfo {
  candidateId: string;
  handle: string;
  version?: string | null;
  updates?: CandidateUpdateNote[];
}

export interface InstalledCandidateSource {
  candidateId?: string;
  candidateHandle?: string;
  candidateVersion?: string;
}

export interface CandidateUpdateAvailability {
  candidateId: string;
  currentVersion: string;
  latestVersion: string;
  latestSummary?: string;
  firstMigration?: string;
  crossedUpdates: CandidateUpdateNote[];
  isMajorUpdate: boolean;
}

const STRICT_SEMVER_PATTERN = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

const parseStrictSemver = (value: string): [number, number, number] | null => {
  const match = STRICT_SEMVER_PATTERN.exec(value.trim());
  if (!match) {
    return null;
  }
  return [Number(match[1]), Number(match[2]), Number(match[3])];
};

export const isStrictSemver = (value: string): boolean =>
  parseStrictSemver(value) !== null;

export const compareStrictSemver = (left: string, right: string): number => {
  const leftParts = parseStrictSemver(left);
  const rightParts = parseStrictSemver(right);
  if (!leftParts || !rightParts) {
    return Number.NaN;
  }

  for (let index = 0; index < leftParts.length; index += 1) {
    const delta = leftParts[index] - rightParts[index];
    if (delta !== 0) {
      return delta > 0 ? 1 : -1;
    }
  }
  return 0;
};

const sortUpdatesDescending = (
  updates: CandidateUpdateNote[],
): CandidateUpdateNote[] =>
  [...updates].sort((left, right) =>
    compareStrictSemver(right.version, left.version),
  );

export const resolveCandidateUpdateAvailability = (
  source: InstalledCandidateSource | undefined,
  candidate: CandidateReleaseInfo | undefined,
): CandidateUpdateAvailability | undefined => {
  if (!source || !candidate?.version) {
    return undefined;
  }

  const sourceMatches =
    source.candidateId === candidate.candidateId ||
    source.candidateHandle === candidate.handle;
  if (!sourceMatches || !source.candidateVersion) {
    return undefined;
  }

  if (
    !isStrictSemver(source.candidateVersion) ||
    !isStrictSemver(candidate.version) ||
    compareStrictSemver(candidate.version, source.candidateVersion) <= 0
  ) {
    return undefined;
  }

  const crossedUpdates = sortUpdatesDescending(candidate.updates ?? []).filter(
    (note) =>
      isStrictSemver(note.version) &&
      compareStrictSemver(note.version, source.candidateVersion ?? "") > 0 &&
      compareStrictSemver(note.version, candidate.version ?? "") <= 0,
  );
  const latestNote = crossedUpdates.find(
    (note) => note.version === candidate.version,
  );
  const currentMajor = parseStrictSemver(source.candidateVersion)?.[0] ?? 0;
  const latestMajor = parseStrictSemver(candidate.version)?.[0] ?? 0;

  return {
    candidateId: candidate.candidateId,
    currentVersion: source.candidateVersion,
    latestVersion: candidate.version,
    latestSummary: latestNote?.summary ?? crossedUpdates[0]?.summary,
    firstMigration: crossedUpdates.find((note) => note.migration)?.migration,
    crossedUpdates,
    isMajorUpdate: latestMajor > currentMajor,
  };
};
