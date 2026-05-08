import type {
  Workflow,
  WorkflowEdge,
  WorkflowNode,
} from "@features/workflow/data/workflow-data";
import type { WorkflowDiffResult, WorkflowSnapshot } from "./workflow-diff";
import type { RJSFSchema } from "@rjsf/utils";

export interface ApiWorkflow {
  id: string;
  handle?: string | null;
  name: string;
  slug: string;
  description: string | null;
  draft_access?: "personal" | "authenticated" | "workspace";
  tags: string[];
  is_archived: boolean;
  is_public: boolean;
  require_login: boolean;
  published_at: string | null;
  published_by: string | null;
  created_at: string;
  updated_at: string;
  share_url?: string | null;
  chatkit?: WorkflowChatKitConfig | null;
  latest_version?: ApiWorkflowVersion | null;
  is_scheduled?: boolean;
}

export interface ChatKitStartScreenPrompt {
  label: string;
  prompt: string;
  icon?: string | null;
}

export interface ChatKitSupportedModel {
  id: string;
  label?: string | null;
  description?: string | null;
  disabled?: boolean;
  default?: boolean;
}

export interface WorkflowChatKitConfig {
  start_screen_prompts?: ChatKitStartScreenPrompt[] | null;
  supported_models?: ChatKitSupportedModel[] | null;
}

export interface ApiWorkflowVersionSummary {
  id: string;
  workflow_id: string;
  version: number;
  mermaid?: string | null;
  has_cron_trigger?: boolean;
  metadata: unknown;
  runnable_config?: WorkflowRunnableConfig | null;
  notes: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ApiWorkflowCanvasData {
  workflow: ApiWorkflow;
  versions?: ApiWorkflowVersionSummary[];
}

export type ApiWorkflowCanvasPayload = ApiWorkflow | ApiWorkflowCanvasData;

export interface PublicWorkflowMetadata {
  id: string;
  handle?: string | null;
  name: string;
  description: string | null;
  is_public: boolean;
  require_login: boolean;
  share_url: string | null;
  chatkit?: WorkflowChatKitConfig | null;
}

export interface WorkflowRunnableConfig {
  configurable?: Record<string, unknown>;
  run_name?: string;
  tags?: string[];
  metadata?: Record<string, unknown>;
  callbacks?: unknown[];
  recursion_limit?: number;
  max_concurrency?: number;
  prompts?: Record<string, unknown>;
}

export interface ApiWorkflowVersion extends ApiWorkflowVersionSummary {
  graph: Record<string, unknown>;
}

export interface ApiWorkflowRun {
  id: string;
  workflow_id: string;
  workflow_version_id: string;
  status: string;
  triggered_by: string;
  input_payload: Record<string, unknown>;
  output_payload?: Record<string, unknown> | null;
  created_at?: string;
  updated_at?: string;
}

export type WorkflowRunRemediationStatus =
  | "pending"
  | "claimed"
  | "fixed"
  | "note_only"
  | "failed"
  | "dismissed";

export type WorkflowRunRemediationClassification =
  | "workflow_fixable"
  | "node_or_edge_bug_workaround"
  | "runtime_or_platform"
  | "external_dependency"
  | "unknown";

export interface ApiWorkflowRunRemediation {
  id: string;
  workflow_id: string;
  workflow_version_id: string;
  run_id: string;
  status: WorkflowRunRemediationStatus;
  fingerprint: string;
  version_checksum: string;
  graph_format?: string | null;
  attempt_count: number;
  classification?: WorkflowRunRemediationClassification | null;
  action?: "create_workflow_version" | "note_only" | null;
  context?: Record<string, unknown>;
  developer_note?: string | null;
  created_version_id?: string | null;
  artifacts?: Record<string, unknown>;
  validation_result?: Record<string, unknown> | null;
  last_error?: string | null;
  claimed_by?: string | null;
  claimed_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface WorkflowCredentialReadinessItem {
  name: string;
  placeholders: string[];
  available: boolean;
  credential_id?: string | null;
  provider?: string | null;
}

export interface WorkflowCredentialReadinessResponse {
  workflow_id: string;
  status: "ready" | "missing" | "not_required";
  referenced_credentials: WorkflowCredentialReadinessItem[];
  available_credentials: string[];
  missing_credentials: string[];
}

export interface WorkflowListenerHealth {
  subscription_id: string;
  node_name: string;
  platform: string;
  status: "active" | "blocked" | "paused" | "error" | "disabled";
  bot_identity_key: string;
  assigned_runtime?: string | null;
  lease_expires_at?: string | null;
  last_event_at?: string | null;
  last_error?: string | null;
  runtime_status:
    | "starting"
    | "healthy"
    | "backoff"
    | "stopped"
    | "error"
    | "unknown";
  runtime_detail?: string | null;
  last_polled_at?: string | null;
  consecutive_failures: number;
}

export interface WorkflowListenerAlert {
  subscription_id: string;
  platform: string;
  kind: "stalled_listener" | "reconnect_loop" | "dispatch_failure";
  detail: string;
}

export interface WorkflowListenerMetricsByPlatform {
  platform: string;
  total: number;
  healthy: number;
  paused: number;
  errors: number;
}

export interface WorkflowListenerMetricsResponse {
  workflow_id: string;
  total_subscriptions: number;
  active_subscriptions: number;
  blocked_subscriptions: number;
  paused_subscriptions: number;
  disabled_subscriptions: number;
  error_subscriptions: number;
  healthy_runtimes: number;
  reconnecting_runtimes: number;
  stalled_listeners: number;
  dispatch_failures: number;
  by_platform: WorkflowListenerMetricsByPlatform[];
  alerts: WorkflowListenerAlert[];
}

export interface WorkflowPublishResponse {
  workflow: ApiWorkflow;
  message?: string | null;
  share_url?: string | null;
}

export interface SystemPluginStatus {
  name: string;
  enabled: boolean;
  status: string;
  version: string;
  exports: string[];
  loaded: boolean;
  load_error?: string | null;
}

export interface SystemPluginsResponse {
  plugins: SystemPluginStatus[];
}

export interface CronTriggerConfig {
  expression: string;
  timezone?: string;
  allow_overlapping?: boolean;
  start_at?: string | null;
  end_at?: string | null;
}

export interface CanvasVersionMetadata {
  snapshot?: WorkflowSnapshot;
  summary?: WorkflowDiffResult["summary"];
  message?: string;
  canvasToGraph?: Record<string, string>;
  graphToCanvas?: Record<string, string>;
  templateId?: string;
  configurableSchemas?: Record<string, RJSFSchema>;
}

export interface RequestOptions extends RequestInit {
  expectJson?: boolean;
}

export interface WorkflowVersionRecord {
  id: string;
  version: string;
  versionNumber: number;
  timestamp: string;
  message: string;
  author: Workflow["owner"];
  summary: WorkflowDiffResult["summary"];
  snapshot: WorkflowSnapshot;
  mermaid?: string | null;
  hasCronTrigger?: boolean;
  runnableConfig?: WorkflowRunnableConfig | null;
  configurableSchemas?: Record<string, RJSFSchema>;
  graphToCanvas?: Record<string, string>;
  templateId?: string;
}

export interface StoredWorkflow extends Workflow {
  versions: WorkflowVersionRecord[];
  draftAccess?: "personal" | "authenticated" | "workspace";
  isArchived?: boolean;
  isPublic?: boolean;
  shareUrl?: string | null;
  chatkitStartScreenPrompts?: ChatKitStartScreenPrompt[] | null;
  chatkitSupportedModels?: ChatKitSupportedModel[] | null;
}

export interface SaveWorkflowInput {
  id?: string;
  name: string;
  description?: string;
  tags?: string[];
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface SaveWorkflowOptions {
  versionMessage?: string;
  actor?: string;
  runnableConfig?: WorkflowRunnableConfig | null;
}
