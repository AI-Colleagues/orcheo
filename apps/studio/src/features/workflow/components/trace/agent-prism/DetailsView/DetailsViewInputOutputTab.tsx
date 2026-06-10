import type { TraceSpan } from "@evilmartians/agent-prism-types";
import type { ReactElement } from "react";
import type { TraceSpanMetadata } from "@features/workflow/pages/workflow-canvas/helpers/trace";

import { useEffect, useMemo, useState } from "react";

import type { TabItem } from "../Tabs";

import { CollapsibleSection } from "../CollapsibleSection";
import { TabSelector } from "../TabSelector";
import {
  DetailsViewContentViewer,
  type DetailsViewContentViewMode,
} from "./DetailsViewContentViewer";

interface DetailsViewInputOutputTabProps {
  data: TraceSpan;
}

type IOSection = "Input" | "Output";
type StateDiffType = "added" | "removed" | "updated";

interface StateDiffEntry {
  path: string;
  type: StateDiffType;
  before?: unknown;
  after?: unknown;
}

const MAX_VISIBLE_DIFF_ENTRIES = 120;

const isObjectRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

const valuesEqual = (left: unknown, right: unknown): boolean => {
  if (Object.is(left, right)) {
    return true;
  }
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
};

const buildStateDiff = (
  before: unknown,
  after: unknown,
  path = "",
): StateDiffEntry[] => {
  if (isObjectRecord(before) && isObjectRecord(after)) {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    const entries: StateDiffEntry[] = [];
    for (const key of Array.from(keys).sort((a, b) => a.localeCompare(b))) {
      const nextPath = path ? `${path}.${key}` : key;
      const hasBefore = Object.prototype.hasOwnProperty.call(before, key);
      const hasAfter = Object.prototype.hasOwnProperty.call(after, key);
      if (!hasBefore && hasAfter) {
        entries.push({
          path: nextPath,
          type: "added",
          after: (after as Record<string, unknown>)[key],
        });
        continue;
      }
      if (hasBefore && !hasAfter) {
        entries.push({
          path: nextPath,
          type: "removed",
          before: (before as Record<string, unknown>)[key],
        });
        continue;
      }
      entries.push(
        ...buildStateDiff(
          (before as Record<string, unknown>)[key],
          (after as Record<string, unknown>)[key],
          nextPath,
        ),
      );
    }
    return entries;
  }

  if (Array.isArray(before) && Array.isArray(after)) {
    const entries: StateDiffEntry[] = [];
    const length = Math.max(before.length, after.length);
    for (let index = 0; index < length; index += 1) {
      const nextPath = `${path}[${index}]`;
      if (index >= before.length) {
        entries.push({
          path: nextPath,
          type: "added",
          after: after[index],
        });
        continue;
      }
      if (index >= after.length) {
        entries.push({
          path: nextPath,
          type: "removed",
          before: before[index],
        });
        continue;
      }
      entries.push(...buildStateDiff(before[index], after[index], nextPath));
    }
    return entries;
  }

  if (valuesEqual(before, after)) {
    return [];
  }
  return [
    {
      path: path || "$",
      type: "updated",
      before,
      after,
    },
  ];
};

const formatPlainContent = (value: unknown): string => {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const formatInlineValue = (value: unknown): string => {
  if (typeof value === "string") {
    return value;
  }
  if (
    typeof value === "number" ||
    typeof value === "boolean" ||
    value == null
  ) {
    return String(value);
  }
  return formatPlainContent(value);
};

export const DetailsViewInputOutputTab = ({
  data,
}: DetailsViewInputOutputTabProps): ReactElement => {
  const metadata = data.metadata as TraceSpanMetadata | undefined;
  const workflowStateBefore = metadata?.workflowStateBefore;
  const workflowStateAfter = metadata?.workflowStateAfter;
  const hasWorkflowState = Boolean(workflowStateBefore || workflowStateAfter);

  const stateDiff = useMemo(
    () => buildStateDiff(workflowStateBefore ?? {}, workflowStateAfter ?? {}),
    [workflowStateBefore, workflowStateAfter],
  );
  const diffSummary = useMemo(
    () =>
      stateDiff.reduce(
        (summary, entry) => {
          if (entry.type === "added") {
            summary.added += 1;
          } else if (entry.type === "removed") {
            summary.removed += 1;
          } else {
            summary.updated += 1;
          }
          return summary;
        },
        { added: 0, removed: 0, updated: 0 },
      ),
    [stateDiff],
  );
  const [showSnapshots, setShowSnapshots] = useState(false);

  useEffect(() => {
    setShowSnapshots(false);
  }, [data.id]);

  if (hasWorkflowState) {
    const visibleDiff = stateDiff.slice(0, MAX_VISIBLE_DIFF_ENTRIES);
    const hiddenCount = Math.max(stateDiff.length - visibleDiff.length, 0);
    const stateInContent = formatPlainContent(workflowStateBefore ?? {});
    const stateOutContent = formatPlainContent(workflowStateAfter ?? {});

    return (
      <div className="space-y-4">
        {(metadata?.workflowStateRedacted ||
          metadata?.workflowStateTruncated) && (
          <div className="border-agentprism-border rounded-md border p-3 text-xs">
            {metadata.workflowStateRedacted && (
              <p className="text-agentprism-muted-foreground">
                Sensitive fields were redacted in this snapshot.
              </p>
            )}
            {metadata.workflowStateTruncated && (
              <p className="text-agentprism-muted-foreground">
                Large values were truncated in this snapshot.
              </p>
            )}
          </div>
        )}

        <CollapsibleSection
          title="State diff"
          defaultOpen
          rightContent={
            <div className="text-agentprism-muted-foreground flex items-center gap-2 text-xs">
              <span className="rounded border border-green-300 px-1.5 py-0.5 text-green-700">
                + {diffSummary.added}
              </span>
              <span className="rounded border border-amber-300 px-1.5 py-0.5 text-amber-700">
                ~ {diffSummary.updated}
              </span>
              <span className="rounded border border-red-300 px-1.5 py-0.5 text-red-700">
                - {diffSummary.removed}
              </span>
            </div>
          }
        >
          {stateDiff.length === 0 ? (
            <p className="text-agentprism-muted-foreground p-3 text-sm">
              No workflow state changes recorded for this span.
            </p>
          ) : (
            <div className="space-y-2">
              {visibleDiff.map((entry) => (
                <div
                  key={`${entry.type}:${entry.path}`}
                  className="border-agentprism-border rounded-md border p-2"
                >
                  <div className="mb-1 flex items-center gap-2 text-xs">
                    <span
                      className={
                        entry.type === "added"
                          ? "rounded border border-green-300 px-1 text-green-700"
                          : entry.type === "removed"
                            ? "rounded border border-red-300 px-1 text-red-700"
                            : "rounded border border-amber-300 px-1 text-amber-700"
                      }
                    >
                      {entry.type}
                    </span>
                    <code className="text-agentprism-foreground break-all">
                      {entry.path}
                    </code>
                  </div>
                  {entry.type !== "added" && (
                    <p className="text-agentprism-muted-foreground break-all text-xs">
                      Before: {formatInlineValue(entry.before)}
                    </p>
                  )}
                  {entry.type !== "removed" && (
                    <p className="text-agentprism-muted-foreground break-all text-xs">
                      After: {formatInlineValue(entry.after)}
                    </p>
                  )}
                </div>
              ))}
              {hiddenCount > 0 && (
                <p className="text-agentprism-muted-foreground px-1 text-xs">
                  Showing first {MAX_VISIBLE_DIFF_ENTRIES} changes (
                  {hiddenCount} more hidden).
                </p>
              )}
            </div>
          )}
        </CollapsibleSection>

        <button
          type="button"
          className="text-agentprism-muted-foreground text-xs underline underline-offset-2"
          onClick={() => setShowSnapshots((current) => !current)}
        >
          {showSnapshots ? "Hide full snapshots" : "Show full snapshots"}
        </button>

        {showSnapshots && (
          <div className="space-y-4">
            <IOSection
              section="Input"
              content={stateInContent}
              parsedContent={stateInContent}
            />
            <IOSection
              section="Output"
              content={stateOutContent}
              parsedContent={stateOutContent}
            />
          </div>
        )}
      </div>
    );
  }

  const hasInput = Boolean(data.input);
  const hasOutput = Boolean(data.output);

  if (!hasInput && !hasOutput) {
    return (
      <div className="border-agentprism-border rounded-md border p-4">
        <p className="text-agentprism-muted-foreground text-sm">
          No input or output data available for this span
        </p>
      </div>
    );
  }

  let parsedInput: string | null = null;
  let parsedOutput: string | null = null;

  if (typeof data.input === "string") {
    try {
      JSON.parse(data.input);
      parsedInput = data.input;
    } catch {
      parsedInput = null;
    }
  }

  if (typeof data.output === "string") {
    try {
      JSON.parse(data.output);
      parsedOutput = data.output;
    } catch {
      parsedOutput = null;
    }
  }

  return (
    <div className="space-y-4">
      {typeof data.input === "string" && (
        <IOSection
          section="Input"
          content={data.input}
          parsedContent={parsedInput}
        />
      )}
      {typeof data.output === "string" && (
        <IOSection
          section="Output"
          content={data.output}
          parsedContent={parsedOutput}
        />
      )}
    </div>
  );
};

interface IOSectionProps {
  section: IOSection;
  content: string;
  parsedContent: string | null;
}

const IOSection = ({
  section,
  content,
  parsedContent,
}: IOSectionProps): ReactElement => {
  const [tab, setTab] = useState<DetailsViewContentViewMode>(
    parsedContent ? "json" : "plain",
  );

  useEffect(() => {
    if (tab === "json" && !parsedContent) {
      setTab("plain");
    }
  }, [tab, parsedContent]);

  const tabItems: TabItem<DetailsViewContentViewMode>[] = [
    { value: "json", label: "JSON", disabled: !parsedContent },
    { value: "plain", label: "Plain" },
  ];

  return (
    <CollapsibleSection
      title={section}
      defaultOpen
      rightContent={
        <TabSelector<DetailsViewContentViewMode>
          items={tabItems}
          defaultValue={parsedContent ? "json" : "plain"}
          value={tab}
          onValueChange={setTab}
          theme="pill"
          onClick={(event) => event.stopPropagation()}
        />
      }
    >
      <DetailsViewContentViewer
        content={content}
        parsedContent={parsedContent}
        mode={tab}
        label={section}
        id={section}
      />
    </CollapsibleSection>
  );
};
