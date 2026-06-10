import mermaid from "mermaid";

const MAX_CONCURRENT_RENDERS = 3;
const MEMORY_CACHE_LIMIT = 150;
const SESSION_CACHE_LIMIT = 40;
const MAX_SESSION_SVG_LENGTH = 150_000;
const SESSION_STORAGE_KEY = "orcheo:workflow:mermaid-svg-cache:v1";

let mermaidInitialized = false;
let hydratedSessionCache = false;
let activeRenders = 0;

const renderQueue: Array<() => void> = [];
const inFlightRenders = new Map<string, Promise<string>>();
const memoryCache = new Map<string, string>();
const sessionCache = new Map<string, string>();

interface MermaidSessionCacheRecord {
  key: string;
  svg: string;
}

export interface MermaidRenderOptions {
  source: string;
  cacheKey: string;
  renderId: string;
  transformSvg?: (svg: string) => string;
}

const ensureMermaidInitialized = () => {
  if (mermaidInitialized) {
    return;
  }

  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "neutral",
  });
  mermaidInitialized = true;
};

const trimCache = <T>(cache: Map<string, T>, maxEntries: number) => {
  while (cache.size > maxEntries) {
    const oldestKey = cache.keys().next().value;
    if (oldestKey === undefined) {
      break;
    }
    cache.delete(oldestKey);
  }
};

const touchCacheEntry = <T>(cache: Map<string, T>, key: string, value: T) => {
  if (cache.has(key)) {
    cache.delete(key);
  }
  cache.set(key, value);
};

const isSessionStorageAvailable = (): boolean =>
  typeof window !== "undefined" && typeof window.sessionStorage !== "undefined";

const logSessionCacheWarning = (
  operation: "read" | "write",
  error: unknown,
) => {
  console.warn(`Failed to ${operation} Mermaid SVG session cache`, error);
};

const readSessionCache = () => {
  if (hydratedSessionCache || !isSessionStorageAvailable()) {
    return;
  }

  hydratedSessionCache = true;

  try {
    const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) {
      return;
    }

    const parsed = JSON.parse(raw) as MermaidSessionCacheRecord[] | undefined;
    if (!Array.isArray(parsed)) {
      return;
    }

    parsed.forEach((record) => {
      if (
        typeof record?.key === "string" &&
        typeof record?.svg === "string" &&
        record.key.length > 0 &&
        record.svg.length > 0
      ) {
        touchCacheEntry(sessionCache, record.key, record.svg);
      }
    });

    trimCache(sessionCache, SESSION_CACHE_LIMIT);
  } catch (error) {
    logSessionCacheWarning("read", error);
  }
};

const writeSessionCache = () => {
  if (!isSessionStorageAvailable()) {
    return;
  }

  try {
    const payload: MermaidSessionCacheRecord[] = Array.from(
      sessionCache.entries(),
    ).map(([key, svg]) => ({ key, svg }));

    window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(payload));
  } catch (error) {
    logSessionCacheWarning("write", error);
  }
};

const getCachedSvg = (cacheKey: string): string | null => {
  const fromMemory = memoryCache.get(cacheKey);
  if (fromMemory) {
    touchCacheEntry(memoryCache, cacheKey, fromMemory);
    return fromMemory;
  }

  readSessionCache();

  const fromSession = sessionCache.get(cacheKey);
  if (!fromSession) {
    return null;
  }

  touchCacheEntry(sessionCache, cacheKey, fromSession);
  touchCacheEntry(memoryCache, cacheKey, fromSession);
  trimCache(memoryCache, MEMORY_CACHE_LIMIT);
  return fromSession;
};

const storeCachedSvg = (cacheKey: string, svg: string) => {
  touchCacheEntry(memoryCache, cacheKey, svg);
  trimCache(memoryCache, MEMORY_CACHE_LIMIT);

  if (svg.length > MAX_SESSION_SVG_LENGTH) {
    return;
  }

  readSessionCache();
  touchCacheEntry(sessionCache, cacheKey, svg);
  trimCache(sessionCache, SESSION_CACHE_LIMIT);
  writeSessionCache();
};

const acquireRenderSlot = async () => {
  if (activeRenders < MAX_CONCURRENT_RENDERS) {
    activeRenders += 1;
    return;
  }

  await new Promise<void>((resolve) => {
    renderQueue.push(() => {
      activeRenders += 1;
      resolve();
    });
  });
};

const releaseRenderSlot = () => {
  activeRenders = Math.max(0, activeRenders - 1);
  const next = renderQueue.shift();
  if (next) {
    next();
  }
};

export const sanitizeMermaidIdPart = (value: string): string =>
  value.replace(/[^a-zA-Z0-9_-]/g, "-");

export const forceMermaidLeftToRight = (source: string): string => {
  const normalizedSource = source.trim();
  if (!normalizedSource) {
    return normalizedSource;
  }

  const withDirection = normalizedSource.replace(
    /^(\s*(?:flowchart|graph)\s+)(?:TB|TD|BT|RL|LR)\b([^\n\r]*)/im,
    "$1LR$2",
  );
  if (withDirection !== normalizedSource) {
    return withDirection;
  }

  return normalizedSource.replace(/^(\s*(?:flowchart|graph))(\s*)$/im, "$1 LR");
};

const MERMAID_COLOR_ALIASES: ReadonlyArray<[RegExp, string]> = [
  [/\bclassDef\s+default\s+fill:#eef4ff\b/gi, "classDef default fill:#f2f0ff"],
  [/\bclassDef\s+last\s+fill:#94b8ff\b/gi, "classDef last fill:#bfb6fc"],
];

export const normalizeMermaidPalette = (source: string): string =>
  MERMAID_COLOR_ALIASES.reduce(
    (normalized, [pattern, replacement]) =>
      normalized.replace(pattern, replacement),
    source,
  );

export const hashMermaidSource = (value: string): string => {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash +=
      (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
  }
  return (hash >>> 0).toString(36);
};

export const buildMermaidCacheKey = (options: {
  scope: string;
  workflowId: string;
  versionId: string;
  source: string;
}): string => {
  const normalizedSource = options.source.trim();
  const sourceHash = hashMermaidSource(normalizedSource);
  return [
    sanitizeMermaidIdPart(options.scope),
    sanitizeMermaidIdPart(options.workflowId),
    sanitizeMermaidIdPart(options.versionId),
    sourceHash,
  ].join(":");
};

export const buildMermaidRenderId = (
  prefix: string,
  cacheKey: string,
): string =>
  `${sanitizeMermaidIdPart(prefix)}-${sanitizeMermaidIdPart(cacheKey)}`;

export const makeMermaidSvgTransparent = (svg: string): string => {
  const svgWithTransparentRoot = svg.replace(
    /<svg\b([^>]*)>/i,
    (match, attributes: string) => {
      const styleMatch = attributes.match(/\sstyle="([^"]*)"/i);
      if (!styleMatch) {
        return `<svg${attributes} style="background-color: transparent;">`;
      }

      const cleanedStyle = styleMatch[1]
        .split(";")
        .map((entry) => entry.trim())
        .filter(Boolean)
        .filter(
          (entry) =>
            !entry.toLowerCase().startsWith("background-color") &&
            !entry.toLowerCase().startsWith("background"),
        )
        .join("; ");
      const nextStyle = ` style="background-color: transparent${cleanedStyle ? `; ${cleanedStyle}` : ""};"`;

      return match.replace(styleMatch[0], nextStyle);
    },
  );

  return svgWithTransparentRoot
    .replace(
      /<rect\b([^>]*\bclass="[^"]*\b(background|canvas)\b[^"]*"[^>]*)\/?>/gi,
      "",
    )
    .replace(
      /<rect\b([^>]*\bid="[^"]*(background|canvas)[^"]*"[^>]*)\/?>/gi,
      "",
    );
};

export const renderMermaidSvg = async ({
  source,
  cacheKey,
  renderId,
  transformSvg,
}: MermaidRenderOptions): Promise<string> => {
  const normalizedSource = source.trim();
  if (!normalizedSource) {
    throw new Error("Mermaid source is empty.");
  }

  const cachedSvg = getCachedSvg(cacheKey);
  if (cachedSvg) {
    return cachedSvg;
  }

  const existingRequest = inFlightRenders.get(cacheKey);
  if (existingRequest) {
    return existingRequest;
  }

  const request = (async () => {
    await acquireRenderSlot();
    try {
      const cachedBeforeRender = getCachedSvg(cacheKey);
      if (cachedBeforeRender) {
        return cachedBeforeRender;
      }

      ensureMermaidInitialized();
      const result = await mermaid.render(renderId, normalizedSource);
      const nextSvg = transformSvg ? transformSvg(result.svg) : result.svg;
      storeCachedSvg(cacheKey, nextSvg);
      return nextSvg;
    } finally {
      releaseRenderSlot();
    }
  })().finally(() => {
    inFlightRenders.delete(cacheKey);
  });

  inFlightRenders.set(cacheKey, request);
  return request;
};

export const __resetMermaidRenderCacheForTests = () => {
  inFlightRenders.clear();
  memoryCache.clear();
  sessionCache.clear();
  renderQueue.length = 0;
  mermaidInitialized = false;
  hydratedSessionCache = false;
  activeRenders = 0;

  if (isSessionStorageAvailable()) {
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
  }
};
