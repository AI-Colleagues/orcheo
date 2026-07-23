import { useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/design-system/ui/dialog";
import { Badge } from "@/design-system/ui/badge";
import { getSystemInfo, type SystemInfoResponse } from "@/lib/api";
import { getStudioVersion } from "@/lib/config";

const UPDATE_CHECK_CACHE_KEY = "orcheo.studio.system_info.v1";
const UPDATE_DISMISS_CACHE_KEY = "orcheo.studio.system_info.dismissed.v1";
const UPDATE_CHECK_TTL_MS = 24 * 60 * 60 * 1000;

interface SystemInfoCachePayload {
  checkedAt: string;
  payload: SystemInfoResponse;
}

interface ParsedSemver {
  major: number;
  minor: number;
  patch: number;
  prerelease: string | null;
}

const parseCache = (raw: string | null): SystemInfoCachePayload | null => {
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<SystemInfoCachePayload>;
    if (!parsed.checkedAt || !parsed.payload) {
      return null;
    }
    return {
      checkedAt: parsed.checkedAt,
      payload: parsed.payload,
    };
  } catch {
    return null;
  }
};

const parseSemver = (value: string | null | undefined): ParsedSemver | null => {
  if (!value) {
    return null;
  }
  const match = value
    .trim()
    .match(/^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z-.]+))?(?:\+.+)?$/);
  if (!match) {
    return null;
  }
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
    prerelease: match[4] ?? null,
  };
};

const compareSemver = (
  current: string | null,
  latest: string | null,
): number => {
  const currentParsed = parseSemver(current);
  const latestParsed = parseSemver(latest);
  if (!currentParsed || !latestParsed) {
    return 0;
  }
  if (currentParsed.major !== latestParsed.major) {
    return currentParsed.major - latestParsed.major;
  }
  if (currentParsed.minor !== latestParsed.minor) {
    return currentParsed.minor - latestParsed.minor;
  }
  if (currentParsed.patch !== latestParsed.patch) {
    return currentParsed.patch - latestParsed.patch;
  }

  if (currentParsed.prerelease === latestParsed.prerelease) {
    return 0;
  }
  if (currentParsed.prerelease === null) {
    return 1;
  }
  if (latestParsed.prerelease === null) {
    return -1;
  }

  const currentParts = currentParsed.prerelease.split(".");
  const latestParts = latestParsed.prerelease.split(".");
  const maxLength = Math.max(currentParts.length, latestParts.length);
  for (let index = 0; index < maxLength; index += 1) {
    const currentPart = currentParts[index];
    const latestPart = latestParts[index];
    if (currentPart === undefined) {
      return -1;
    }
    if (latestPart === undefined) {
      return 1;
    }
    if (currentPart === latestPart) {
      continue;
    }

    const currentNumber = Number(currentPart);
    const latestNumber = Number(latestPart);
    const currentIsNumeric = !Number.isNaN(currentNumber);
    const latestIsNumeric = !Number.isNaN(latestNumber);
    if (currentIsNumeric && latestIsNumeric) {
      return currentNumber - latestNumber;
    }
    if (currentIsNumeric) {
      return -1;
    }
    if (latestIsNumeric) {
      return 1;
    }
    return currentPart.localeCompare(latestPart);
  }
  return 0;
};

const isDismissed = (): boolean => {
  if (typeof window === "undefined") {
    return false;
  }
  const raw = window.localStorage.getItem(UPDATE_DISMISS_CACHE_KEY);
  if (!raw) {
    return false;
  }
  const dismissedAt = Date.parse(raw);
  if (Number.isNaN(dismissedAt)) {
    return false;
  }
  return Date.now() - dismissedAt < UPDATE_CHECK_TTL_MS;
};

interface AboutDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export default function AboutDialog({ open, onOpenChange }: AboutDialogProps) {
  const [cachedInfo, setCachedInfo] = useState<SystemInfoResponse | null>(
    null,
  );
  const [liveBackendVersion, setLiveBackendVersion] = useState<string | null>(
    null,
  );
  const [dismissedReminder, setDismissedReminder] = useState<boolean>(() =>
    isDismissed(),
  );
  const studioVersion = getStudioVersion();

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const cache = parseCache(
      window.localStorage.getItem(UPDATE_CHECK_CACHE_KEY),
    );
    if (cache) {
      setCachedInfo(cache.payload);
      setLiveBackendVersion(cache.payload.backend.current_version);
      const cacheAge = Date.now() - Date.parse(cache.checkedAt);
      if (cacheAge < UPDATE_CHECK_TTL_MS) {
        return;
      }
    }

    let active = true;
    void getSystemInfo()
      .then((payload) => {
        if (!active) {
          return;
        }
        setLiveBackendVersion(payload.backend.current_version);
        setCachedInfo(payload);
        const cachePayload: SystemInfoCachePayload = {
          checkedAt: new Date().toISOString(),
          payload,
        };
        window.localStorage.setItem(
          UPDATE_CHECK_CACHE_KEY,
          JSON.stringify(cachePayload),
        );
      })
      .catch(() => {
        // Keep UI silent when backend metadata is temporarily unavailable.
      });

    return () => {
      active = false;
    };
  }, [open]);

  const studioUpdateAvailable = useMemo(() => {
    if (!cachedInfo) {
      return false;
    }
    if (cachedInfo.studio.update_available) {
      return true;
    }
    return compareSemver(studioVersion, cachedInfo.studio.latest_version) < 0;
  }, [studioVersion, cachedInfo]);

  const showReminder =
    ((cachedInfo?.backend.update_available ?? false) ||
      studioUpdateAvailable) &&
    !dismissedReminder;

  const updateLines = useMemo(() => {
    if (!cachedInfo) return [];
    const lines: string[] = [];
    if (cachedInfo.backend.update_available) {
      lines.push(
        `Backend: ${cachedInfo.backend.current_version} → ${cachedInfo.backend.latest_version}`,
      );
    }
    if (studioUpdateAvailable) {
      const studioCurrent = cachedInfo.studio.current_version ?? studioVersion;
      lines.push(
        `Orcheo: ${studioCurrent} → ${cachedInfo.studio.latest_version}`,
      );
    }
    return lines;
  }, [studioUpdateAvailable, studioVersion, cachedInfo]);

  const dismissUpdateReminder = () => {
    window.localStorage.setItem(
      UPDATE_DISMISS_CACHE_KEY,
      new Date().toISOString(),
    );
    setDismissedReminder(true);
  };

  const rows = [
    { label: "Studio", value: studioVersion },
    { label: "Backend", value: liveBackendVersion ?? "unknown" },
    { label: "CLI", value: cachedInfo?.cli.current_version ?? "unknown" },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogTitle className="sr-only">About Orcheo</DialogTitle>
        <DialogDescription className="sr-only">
          Studio, backend, and CLI version information.
        </DialogDescription>
        <div className="flex items-center gap-3">
          <img
            src="/favicon.ico"
            alt="Orcheo"
            className="h-9 w-9 rounded-md"
          />
          <div>
            <div className="font-semibold text-foreground">Orcheo</div>
            <div className="text-xs text-muted-foreground">
              by AI Colleagues
            </div>
          </div>
        </div>

        <div className="overflow-hidden rounded-md border border-border">
          <table className="w-full text-sm">
            <tbody>
              {rows.map((row) => (
                <tr key={row.label} className="border-b border-border last:border-0">
                  <td className="px-3 py-2 text-muted-foreground">
                    {row.label}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs text-foreground">
                    {row.value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {showReminder && (
          <div className="space-y-1 rounded-md border border-border bg-muted/40 p-3 text-xs">
            <Badge variant="secondary" className="text-[10px]">
              Update available
            </Badge>
            {updateLines.map((line) => (
              <p key={line} className="text-muted-foreground">
                {line}
              </p>
            ))}
            <p className="text-muted-foreground">
              Run: orcheo install upgrade
            </p>
            <button
              type="button"
              className="text-left text-muted-foreground underline"
              onClick={dismissUpdateReminder}
            >
              Remind me tomorrow
            </button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
