import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, RefreshCw, Search, Wrench } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/design-system/ui/alert";
import { Badge } from "@/design-system/ui/badge";
import { Button } from "@/design-system/ui/button";
import { Input } from "@/design-system/ui/input";
import SidebarLayout from "@features/workflow/components/layouts/sidebar-layout";
import WorkflowPageLayout from "@features/workflow/components/layouts/workflow-page-layout";
import TopNavigation from "@features/shared/components/top-navigation";
import { usePageContext } from "@/hooks/use-page-context";
import { listWorkflows } from "@features/workflow/lib/workflow-storage";
import type { StoredWorkflow } from "@features/workflow/lib/workflow-storage";
import { fetchWorkflowRemediations } from "@features/workflow/lib/workflow-storage-api";
import type { ApiWorkflowRunRemediation } from "@features/workflow/lib/workflow-storage.types";
import {
  filterRemediations,
  formatRemediationTimestamp,
  sortRemediationsByUpdatedAt,
  summarizeRemediationNote,
  type RemediationStatusFilter,
} from "./workflow-remediations.helpers";
import { cn } from "@/lib/utils";

const STATUS_FILTERS: Array<{
  label: string;
  value: RemediationStatusFilter;
}> = [
  { label: "All", value: "all" },
  { label: "Pending", value: "pending" },
  { label: "Claimed", value: "claimed" },
  { label: "Fixed", value: "fixed" },
  { label: "Note only", value: "note_only" },
  { label: "Failed", value: "failed" },
  { label: "Dismissed", value: "dismissed" },
];

const getWorkflowName = (
  remediation: ApiWorkflowRunRemediation,
  workflowsById: Map<string, StoredWorkflow>,
): string => workflowsById.get(remediation.workflow_id)?.name ?? remediation.workflow_id;

const formatLine = (value: string | null | undefined): string =>
  value?.trim() ? value : "Not recorded";

const DetailLabel = ({ label, value }: { label: string; value: string }) => (
  <div className="space-y-1">
    <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
      {label}
    </p>
    <p className="text-sm text-foreground">{value}</p>
  </div>
);

export default function WorkflowRemediationsPage() {
  const { setPageContext } = usePageContext();
  useEffect(() => {
    setPageContext({ page: "remediations" });
  }, [setPageContext]);

  const [remediations, setRemediations] = useState<ApiWorkflowRunRemediation[]>(
    [],
  );
  const [workflows, setWorkflows] = useState<StoredWorkflow[]>([]);
  const [selectedRemediationId, setSelectedRemediationId] = useState<
    string | null
  >(null);
  const [statusFilter, setStatusFilter] = useState<RemediationStatusFilter>(
    "all",
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRemediations = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (mode === "refresh") {
        setIsRefreshing(true);
      } else {
        setIsLoading(true);
      }

      setError(null);
      try {
        const [workflowResults, remediationResults] = await Promise.allSettled([
          listWorkflows({ forceRefresh: true }),
          fetchWorkflowRemediations(),
        ]);

        const nextWorkflows =
          workflowResults.status === "fulfilled" ? workflowResults.value : [];
        const nextRemediations =
          remediationResults.status === "fulfilled"
            ? remediationResults.value
            : [];

        const failureMessages = [
          workflowResults.status === "rejected"
            ? workflowResults.reason instanceof Error
              ? workflowResults.reason.message
              : "Failed to load workflows."
            : null,
          remediationResults.status === "rejected"
            ? remediationResults.reason instanceof Error
              ? remediationResults.reason.message
              : "Failed to load remediations."
            : null,
        ].filter((value): value is string => Boolean(value));

        setWorkflows(nextWorkflows);
        setRemediations(sortRemediationsByUpdatedAt(nextRemediations));
        setError(failureMessages.join(" ") || null);
      } finally {
        if (mode === "refresh") {
          setIsRefreshing(false);
        } else {
          setIsLoading(false);
        }
      }
    },
    [],
  );

  useEffect(() => {
    void loadRemediations();
  }, [loadRemediations]);

  const workflowsById = useMemo(
    () => new Map(workflows.map((workflow) => [workflow.id, workflow])),
    [workflows],
  );

  const filteredRemediations = useMemo(
    () =>
      filterRemediations(remediations, {
        query: searchQuery,
        statusFilter,
        workflowNamesById: new Map(
          workflows.map((workflow) => [workflow.id, workflow.name]),
        ),
      }),
    [remediations, searchQuery, statusFilter, workflows],
  );

  useEffect(() => {
    if (filteredRemediations.length === 0) {
      setSelectedRemediationId(null);
      return;
    }
    if (
      !selectedRemediationId ||
      !filteredRemediations.some((remediation) => remediation.id === selectedRemediationId)
    ) {
      setSelectedRemediationId(filteredRemediations[0]?.id ?? null);
    }
  }, [filteredRemediations, selectedRemediationId]);

  const selectedRemediation = useMemo(
    () =>
      filteredRemediations.find(
        (remediation) => remediation.id === selectedRemediationId,
      ) ?? null,
    [filteredRemediations, selectedRemediationId],
  );

  const countsByStatus = useMemo(() => {
    const counts = new Map<RemediationStatusFilter, number>();
    counts.set("all", remediations.length);
    for (const remediation of remediations) {
      counts.set(remediation.status, (counts.get(remediation.status) ?? 0) + 1);
    }
    return counts;
  }, [remediations]);

  const selectedWorkflowName = selectedRemediation
    ? getWorkflowName(selectedRemediation, workflowsById)
    : null;

  return (
    <WorkflowPageLayout header={<TopNavigation />}>
      <div className="flex h-full min-h-0 flex-col overflow-hidden p-4">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1">
            <p className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
              Operations
            </p>
            <h1 className="text-2xl font-semibold text-foreground">
              Remediation records
            </h1>
            <p className="max-w-2xl text-sm text-muted-foreground">
              Review Orcheo Vibe remediation notes, validation output, and
              created workflow versions in one place.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="outline">
              <Link to="/workflow-canvas">
                <ExternalLink className="mr-2 h-4 w-4" />
                Open canvas
              </Link>
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                void loadRemediations("refresh");
              }}
              disabled={isRefreshing}
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              {isRefreshing ? "Refreshing..." : "Refresh"}
            </Button>
          </div>
        </div>

        <div className="mb-4 flex flex-wrap items-center gap-2">
          {STATUS_FILTERS.map((filter) => (
            <Button
              key={filter.value}
              variant={statusFilter === filter.value ? "default" : "outline"}
              size="sm"
              onClick={() => setStatusFilter(filter.value)}
            >
              {filter.label}
              <Badge variant="secondary" className="ml-2">
                {countsByStatus.get(filter.value) ?? 0}
              </Badge>
            </Button>
          ))}
          <div className="ml-auto flex min-w-0 items-center gap-2">
            <Search className="h-4 w-4 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search workflow, note, run id, or status"
              className="w-[280px]"
            />
          </div>
        </div>

        {error && (
          <Alert variant="destructive" className="mb-4">
            <AlertTitle>Partial load failure</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <SidebarLayout
          sidebar={
            <div className="flex h-full min-h-0 flex-col gap-3 p-4">
              <div className="space-y-1">
                <h2 className="text-sm font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                  Records
                </h2>
                <p className="text-sm text-muted-foreground">
                  {isLoading
                    ? "Loading remediation history..."
                    : `${filteredRemediations.length} record${filteredRemediations.length === 1 ? "" : "s"} shown`}
                </p>
              </div>

              <div className="min-h-0 flex-1 space-y-2 overflow-auto pr-1">
                {filteredRemediations.map((remediation) => {
                  const workflowName = getWorkflowName(remediation, workflowsById);
                  const noteSummary = summarizeRemediationNote(
                    remediation.developer_note,
                  );
                  const isSelected = remediation.id === selectedRemediationId;

                  return (
                    <button
                      key={remediation.id}
                      type="button"
                      onClick={() => setSelectedRemediationId(remediation.id)}
                      className={cn(
                        "w-full rounded-lg border p-3 text-left transition-colors",
                        isSelected
                          ? "border-primary bg-primary/5"
                          : "border-border bg-background hover:border-primary/40 hover:bg-muted/30",
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 space-y-1">
                          <p className="truncate text-sm font-semibold text-foreground">
                            {workflowName}
                          </p>
                          <p className="truncate text-xs text-muted-foreground">
                            Run {remediation.run_id.slice(0, 8)} · Version{" "}
                            {remediation.workflow_version_id.slice(0, 8)}
                          </p>
                        </div>
                        <Badge variant="outline">{remediation.status}</Badge>
                      </div>

                      <div className="mt-2 flex flex-wrap gap-2">
                        {remediation.classification && (
                          <Badge variant="secondary">
                            {remediation.classification}
                          </Badge>
                        )}
                        {remediation.action && (
                          <Badge variant="secondary">{remediation.action}</Badge>
                        )}
                      </div>

                      <p className="mt-3 line-clamp-3 text-sm text-muted-foreground">
                        {noteSummary}
                      </p>

                      <p className="mt-3 text-xs text-muted-foreground">
                        Updated {formatRemediationTimestamp(remediation.updated_at)}
                      </p>
                    </button>
                  );
                })}

                {!isLoading && filteredRemediations.length === 0 && (
                  <div className="rounded-lg border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">
                    No remediation records match the current filters.
                  </div>
                )}
              </div>
            </div>
          }
          sidebarWidth={360}
          onWidthChange={() => {}}
          showCollapseButton={false}
          resizable
          minWidth={280}
          maxWidth={560}
          className="h-full min-h-0"
        >
          <div className="flex h-full min-h-0 flex-col overflow-hidden">
            {!selectedRemediation ? (
              <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-border bg-card text-sm text-muted-foreground">
                {isLoading
                  ? "Loading remediation details..."
                  : "Select a remediation record to inspect the note and metadata."}
              </div>
            ) : (
              <div className="flex h-full min-h-0 flex-col gap-4 overflow-auto rounded-lg border border-border bg-card p-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Wrench className="h-4 w-4 text-muted-foreground" />
                      <h2 className="truncate text-xl font-semibold text-foreground">
                        {selectedWorkflowName}
                      </h2>
                      <Badge variant="outline">{selectedRemediation.status}</Badge>
                      {selectedRemediation.classification && (
                        <Badge variant="secondary">
                          {selectedRemediation.classification}
                        </Badge>
                      )}
                    </div>
                    <p className="text-sm text-muted-foreground">
                      Run {selectedRemediation.run_id} · Workflow version{" "}
                      {selectedRemediation.workflow_version_id}
                    </p>
                  </div>

                  <Button asChild variant="outline">
                    <Link to={`/workflow-canvas/${selectedRemediation.workflow_id}`}>
                      <ExternalLink className="mr-2 h-4 w-4" />
                      Open workflow
                    </Link>
                  </Button>
                </div>

                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  <DetailLabel
                    label="Created version"
                    value={formatLine(selectedRemediation.created_version_id)}
                  />
                  <DetailLabel
                    label="Attempt count"
                    value={String(selectedRemediation.attempt_count)}
                  />
                  <DetailLabel
                    label="Agent action"
                    value={formatLine(selectedRemediation.action)}
                  />
                  <DetailLabel
                    label="Claimed by"
                    value={formatLine(selectedRemediation.claimed_by)}
                  />
                  <DetailLabel
                    label="Claimed at"
                    value={formatRemediationTimestamp(selectedRemediation.claimed_at)}
                  />
                  <DetailLabel
                    label="Updated at"
                    value={formatRemediationTimestamp(selectedRemediation.updated_at)}
                  />
                  <DetailLabel
                    label="Fingerprint"
                    value={selectedRemediation.fingerprint.slice(0, 24)}
                  />
                  <DetailLabel
                    label="Version checksum"
                    value={selectedRemediation.version_checksum.slice(0, 24)}
                  />
                  <DetailLabel
                    label="Graph format"
                    value={formatLine(selectedRemediation.graph_format)}
                  />
                </div>

                {selectedRemediation.developer_note && (
                  <section className="space-y-2">
                    <h3 className="text-sm font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                      Developer note
                    </h3>
                    <div className="rounded-lg border border-border bg-muted/30 p-4 text-sm leading-6 text-foreground whitespace-pre-wrap">
                      {selectedRemediation.developer_note}
                    </div>
                  </section>
                )}

                {selectedRemediation.last_error && (
                  <section className="space-y-2">
                    <h3 className="text-sm font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                      Last error
                    </h3>
                    <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm leading-6 text-destructive whitespace-pre-wrap">
                      {selectedRemediation.last_error}
                    </div>
                  </section>
                )}

                <section className="grid gap-4 xl:grid-cols-2">
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                      Artifacts
                    </h3>
                    <pre className="max-h-[320px] overflow-auto rounded-lg border border-border bg-muted/20 p-4 text-xs text-foreground">
                      {JSON.stringify(selectedRemediation.artifacts ?? {}, null, 2)}
                    </pre>
                  </div>
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                      Validation result
                    </h3>
                    <pre className="max-h-[320px] overflow-auto rounded-lg border border-border bg-muted/20 p-4 text-xs text-foreground">
                      {JSON.stringify(
                        selectedRemediation.validation_result ?? {},
                        null,
                        2,
                      )}
                    </pre>
                  </div>
                </section>

                <section className="space-y-2">
                  <h3 className="text-sm font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                    Context
                  </h3>
                  <pre className="max-h-[320px] overflow-auto rounded-lg border border-border bg-muted/20 p-4 text-xs text-foreground">
                    {JSON.stringify(selectedRemediation.context ?? {}, null, 2)}
                  </pre>
                </section>
              </div>
            )}
          </div>
        </SidebarLayout>
      </div>
    </WorkflowPageLayout>
  );
}
