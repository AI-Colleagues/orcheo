import avatar01 from "./avatar-01.svg";
import avatar02 from "./avatar-02.svg";
import avatar03 from "./avatar-03.svg";
import avatar04 from "./avatar-04.svg";
import avatar05 from "./avatar-05.svg";
import avatar06 from "./avatar-06.svg";
import avatar07 from "./avatar-07.svg";
import avatar08 from "./avatar-08.svg";
import avatar09 from "./avatar-09.svg";
import avatar10 from "./avatar-10.svg";
import avatar11 from "./avatar-11.svg";
import avatar12 from "./avatar-12.svg";
import avatar13 from "./avatar-13.svg";
import avatar14 from "./avatar-14.svg";
import avatar15 from "./avatar-15.svg";
import avatar16 from "./avatar-16.svg";
import avatar17 from "./avatar-17.svg";
import avatar18 from "./avatar-18.svg";
import avatar19 from "./avatar-19.svg";
import avatar20 from "./avatar-20.svg";
import avatar21 from "./avatar-21.svg";

export const AVATAR_IDS = [
  "avatar-01",
  "avatar-02",
  "avatar-03",
  "avatar-04",
  "avatar-05",
  "avatar-06",
  "avatar-07",
  "avatar-08",
  "avatar-09",
  "avatar-10",
  "avatar-11",
  "avatar-12",
  "avatar-13",
  "avatar-14",
  "avatar-15",
  "avatar-16",
  "avatar-17",
  "avatar-18",
  "avatar-19",
  "avatar-20",
  "avatar-21",
] as const;

export type AvatarId = (typeof AVATAR_IDS)[number];

const AVATAR_URLS: Record<AvatarId, string> = {
  "avatar-01": avatar01,
  "avatar-02": avatar02,
  "avatar-03": avatar03,
  "avatar-04": avatar04,
  "avatar-05": avatar05,
  "avatar-06": avatar06,
  "avatar-07": avatar07,
  "avatar-08": avatar08,
  "avatar-09": avatar09,
  "avatar-10": avatar10,
  "avatar-11": avatar11,
  "avatar-12": avatar12,
  "avatar-13": avatar13,
  "avatar-14": avatar14,
  "avatar-15": avatar15,
  "avatar-16": avatar16,
  "avatar-17": avatar17,
  "avatar-18": avatar18,
  "avatar-19": avatar19,
  "avatar-20": avatar20,
  "avatar-21": avatar21,
};

const isAvatarId = (value: string): value is AvatarId =>
  (AVATAR_IDS as readonly string[]).includes(value);

/** Deterministic avatar selection seeded by a stable string (e.g. workflow id). */
export const seededAvatarId = (seed: string): AvatarId => {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = ((hash << 5) - hash + seed.charCodeAt(i)) | 0;
  }
  return AVATAR_IDS[Math.abs(hash) % AVATAR_IDS.length];
};

/**
 * Resolve an avatar identifier to a URL.
 * Accepts an explicit avatar ID ("avatar-01"), "random", or absent values —
 * the latter two fall back to a deterministic avatar derived from `seed`.
 */
export const resolveAvatarUrl = (
  avatar: string | null | undefined,
  seed: string,
): string => {
  const id =
    avatar && avatar !== "random" && isAvatarId(avatar)
      ? avatar
      : seededAvatarId(seed);
  return AVATAR_URLS[id];
};
