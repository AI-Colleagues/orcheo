const FRONTMATTER_START_RE = /^# \/\/\/ (?<type>[a-zA-Z0-9_-]+)[ \t]*$/;
const FRONTMATTER_END_RE = /^# \/\/\/[ \t]*$/;

const STRING_FIELDS = new Set([
  "name",
  "id",
  "handle",
  "description",
  "entrypoint",
]);

export interface WorkflowFrontmatter {
  name?: string;
  id?: string;
  handle?: string;
  description?: string;
  entrypoint?: string;
}

const parseTomlString = (value: string): string | undefined => {
  const trimmed = value.trim();
  if (trimmed.startsWith('"') && trimmed.endsWith('"')) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      return typeof parsed === "string" && parsed.trim() ? parsed.trim() : undefined;
    } catch {
      return undefined;
    }
  }
  if (trimmed.startsWith("'") && trimmed.endsWith("'")) {
    const parsed = trimmed.slice(1, -1);
    return parsed.trim() ? parsed.trim() : undefined;
  }
  return undefined;
};

const collectOrcheoFrontmatter = (source: string): string[] | undefined => {
  const blocks: string[][] = [];
  let currentLines: string[] = [];
  let currentType: string | undefined;
  let inBlock = false;

  for (const line of source.split(/\r?\n/)) {
    if (inBlock) {
      if (FRONTMATTER_END_RE.test(line)) {
        if (currentType === "orcheo") {
          blocks.push(currentLines);
        }
        currentLines = [];
        currentType = undefined;
        inBlock = false;
        continue;
      }
      currentLines.push(line);
      continue;
    }

    const match = line.match(FRONTMATTER_START_RE);
    if (match?.groups?.type) {
      currentType = match.groups.type;
      currentLines = [];
      inBlock = true;
    }
  }

  if (blocks.length !== 1) {
    return undefined;
  }
  return blocks[0];
};

const toTomlLines = (contentLines: string[]): string[] =>
  contentLines.flatMap((line) => {
    if (!line.startsWith("#")) {
      return [];
    }
    let stripped = line.slice(1);
    if (stripped.startsWith(" ") || stripped.startsWith("\t")) {
      stripped = stripped.slice(1);
    }
    return [stripped];
  });

export const parseWorkflowFrontmatter = (
  source: string,
): WorkflowFrontmatter => {
  const contentLines = collectOrcheoFrontmatter(source);
  if (!contentLines) {
    return {};
  }

  const frontmatter: WorkflowFrontmatter = {};
  for (const line of toTomlLines(contentLines)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const match = trimmed.match(/^([A-Za-z0-9_-]+)\s*=\s*(.+)$/);
    const key = match?.[1];
    if (!key || !STRING_FIELDS.has(key)) {
      continue;
    }
    const value = parseTomlString(match[2] ?? "");
    if (!value) {
      continue;
    }
    frontmatter[key as keyof WorkflowFrontmatter] = value;
  }
  return frontmatter;
};
