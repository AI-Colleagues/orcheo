import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/design-system/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/design-system/ui/tooltip";
import { getSystemInfo, type SystemInfoResponse } from "@/lib/api";
import { getCanvasVersion } from "@/lib/config";

const UPDATE_CHECK_CACHE_KEY = "orcheo.canvas.system_info.v1";
const UPDATE_DISMISS_CACHE_KEY = "orcheo.canvas.system_info.dismissed.v1";
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

const shouldRefresh = (cache: SystemInfoCachePayload | null): boolean => {
  if (!cache) {
    return true;
  }
  const lastCheckedAt = Date.parse(cache.checkedAt);
  if (Number.isNaN(lastCheckedAt)) {
    return true;
  }
  return Date.now() - lastCheckedAt >= UPDATE_CHECK_TTL_MS;
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

export default function VersionStatus() {
  const [systemInfo, setSystemInfo] = useState<SystemInfoResponse | null>(null);
  const [dismissedReminder, setDismissedReminder] = useState<boolean>(() =>
    isDismissed(),
  );
  const canvasVersion = getCanvasVersion();

  useEffect(() => {
    const cache = parseCache(
      window.localStorage.getItem(UPDATE_CHECK_CACHE_KEY),
    );
    if (cache) {
      setSystemInfo(cache.payload);
    }

    if (!shouldRefresh(cache)) {
      return;
    }

    let active = true;
    void getSystemInfo()
      .then((payload) => {
        if (!active) {
          return;
        }
        setSystemInfo(payload);
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
  }, []);

  const canvasUpdateAvailable = useMemo(() => {
    if (!systemInfo) {
      return false;
    }
    if (systemInfo.canvas.update_available) {
      return true;
    }
    return compareSemver(canvasVersion, systemInfo.canvas.latest_version) < 0;
  }, [canvasVersion, systemInfo]);

  const showReminder =
    ((systemInfo?.backend.update_available ?? false) ||
      canvasUpdateAvailable) &&
    !dismissedReminder;

  const versionSummary = useMemo(() => {
    const backend = systemInfo?.backend.current_version ?? "unknown";
    return `Orcheo ${canvasVersion} · Backend ${backend}`;
  }, [canvasVersion, systemInfo]);

  const updateLines = useMemo(() => {
    if (!systemInfo) return [];
    const lines: string[] = [];
    if (systemInfo.backend.update_available) {
      lines.push(
        `Backend: ${systemInfo.backend.current_version} → ${systemInfo.backend.latest_version}`,
      );
    }
    if (canvasUpdateAvailable) {
      const canvasCurrent = systemInfo.canvas.current_version ?? canvasVersion;
      lines.push(
        `Orcheo: ${canvasCurrent} → ${systemInfo.canvas.latest_version}`,
      );
    }
    return lines;
  }, [canvasUpdateAvailable, canvasVersion, systemInfo]);

  const dismissUpdateReminder = () => {
    if (typeof window === "undefined") {
      return;
    }
    window.localStorage.setItem(
      UPDATE_DISMISS_CACHE_KEY,
      new Date().toISOString(),
    );
    setDismissedReminder(true);
  };

  return (
    <div
      className="hidden md:flex items-center gap-2"
      aria-label="Version status"
    >
      <span className="text-xs text-muted-foreground">{versionSummary}</span>
      {showReminder && (
        <TooltipProvider delayDuration={300}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="secondary" className="text-[10px] cursor-default">
                Update available
              </Badge>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-xs">
              <div className="space-y-1 text-xs">
                {updateLines.map((line) => (
                  <p key={line}>{line}</p>
                ))}
                <p className="pt-1 text-muted-foreground">
                  Run: orcheo install upgrade
                </p>
                <button
                  type="button"
                  className="pt-1 text-left text-muted-foreground underline"
                  onClick={dismissUpdateReminder}
                >
                  Remind me tomorrow
                </button>
              </div>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}
    </div>
  );
}
