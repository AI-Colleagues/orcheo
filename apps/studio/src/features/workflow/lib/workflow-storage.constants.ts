import {
  SAMPLE_WORKFLOWS,
  type Workflow,
} from "@features/workflow/data/workflow-data";
import type { WorkflowDiffResult } from "./workflow-diff";

export const DEFAULT_OWNER: Workflow["owner"] = SAMPLE_WORKFLOWS[0]?.owner ?? {
  id: "canvas-owner",
  name: "Canvas Author",
  avatar: "https://avatar.vercel.sh/orcheo",
};

export const HISTORY_LIMIT = 20;
export const DEFAULT_ACTOR = "canvas-app";
export const DEFAULT_SUMMARY: WorkflowDiffResult["summary"] = {
  added: 0,
  removed: 0,
  modified: 0,
};

export const WORKFLOW_STORAGE_EVENT = "orcheo:workflows-updated";
