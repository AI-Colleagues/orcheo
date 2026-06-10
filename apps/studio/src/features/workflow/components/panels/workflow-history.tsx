import React, { useEffect, useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import { computeWorkflowDiff } from "@features/workflow/lib/workflow-diff";
import type { WorkflowVersionRecord } from "@features/workflow/lib/workflow-storage";

import WorkflowDiffDialog, {
  type WorkflowDiffContext,
} from "./workflow-diff-dialog";
import WorkflowHistoryFilters from "./workflow-history-filters";
import WorkflowHistoryFooter from "./workflow-history-footer";
import WorkflowHistoryHeader from "./workflow-history-header";
import WorkflowHistoryTable from "./workflow-history-table";

interface WorkflowHistoryProps {
  versions?: WorkflowVersionRecord[];
  currentVersion?: string;
  onRestoreVersion?: (versionId: string) => void;
  className?: string;
}

export default function WorkflowHistory({
  versions = [],
  currentVersion,
  onRestoreVersion,
  className,
}: WorkflowHistoryProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(
    null,
  );
  const [compareVersionId, setCompareVersionId] = useState<string | null>(null);
  const [showDiffDialog, setShowDiffDialog] = useState(false);
  const [diffContext, setDiffContext] = useState<WorkflowDiffContext | null>(
    null,
  );

  useEffect(() => {
    if (!versions.length) {
      setSelectedVersionId(null);
      setCompareVersionId(null);
      return;
    }

    if (currentVersion) {
      const matched = versions.find(
        (version) => version.version === currentVersion,
      );
      setSelectedVersionId(matched?.id ?? null);
    } else if (!selectedVersionId) {
      setSelectedVersionId(versions[0].id);
    }
  }, [currentVersion, selectedVersionId, versions]);

  // Filter versions based on search query
  const filteredVersions = useMemo(() => {
    if (!searchQuery) {
      return versions;
    }
    return versions.filter(
      (version) =>
        version.version.toLowerCase().includes(searchQuery.toLowerCase()) ||
        version.message.toLowerCase().includes(searchQuery.toLowerCase()) ||
        version.author.name.toLowerCase().includes(searchQuery.toLowerCase()),
    );
  }, [searchQuery, versions]);

  const selectedVersionRecord = useMemo(() => {
    return versions.find((version) => version.id === selectedVersionId) ?? null;
  }, [selectedVersionId, versions]);

  const compareVersionRecord = useMemo(() => {
    return versions.find((version) => version.id === compareVersionId) ?? null;
  }, [compareVersionId, versions]);

  const handleSelectVersion = (versionId: string) => {
    setSelectedVersionId(versionId);
    if (compareVersionId === versionId) {
      setCompareVersionId(null);
    }
  };

  const handleCompareVersions = () => {
    if (!selectedVersionRecord || !compareVersionRecord) {
      return;
    }
    const diffResult = computeWorkflowDiff(
      selectedVersionRecord.snapshot,
      compareVersionRecord.snapshot,
    );
    setDiffContext({
      base: selectedVersionRecord,
      target: compareVersionRecord,
      result: diffResult,
    });
    setShowDiffDialog(true);
  };

  const handleRestoreVersion = () => {
    if (selectedVersionId) {
      onRestoreVersion?.(selectedVersionId);
    }
  };

  return (
    <div
      className={cn(
        "flex flex-col border border-border rounded-lg bg-background shadow-lg",
        className,
      )}
    >
      <WorkflowHistoryHeader
        onRestoreVersion={handleRestoreVersion}
        onCompareVersions={handleCompareVersions}
        canRestore={
          !!selectedVersionRecord &&
          (!currentVersion || selectedVersionRecord.version !== currentVersion)
        }
        canCompare={!!selectedVersionRecord && !!compareVersionRecord}
      />

      <WorkflowHistoryFilters
        searchQuery={searchQuery}
        onSearchChange={(value) => setSearchQuery(value)}
        compareVersionId={compareVersionId}
        onSelectCompareVersion={(value) => setCompareVersionId(value)}
        versions={versions}
        selectedVersionId={selectedVersionId}
      />

      <WorkflowHistoryTable
        filteredVersions={filteredVersions}
        selectedVersionId={selectedVersionId}
        currentVersion={currentVersion}
        searchQuery={searchQuery}
        onSelectVersion={handleSelectVersion}
      />

      <WorkflowHistoryFooter totalVersions={filteredVersions.length} />

      <WorkflowDiffDialog
        open={showDiffDialog}
        onOpenChange={setShowDiffDialog}
        diffContext={diffContext}
        selectedVersionRecord={selectedVersionRecord}
        compareVersionRecord={compareVersionRecord}
      />
    </div>
  );
}
