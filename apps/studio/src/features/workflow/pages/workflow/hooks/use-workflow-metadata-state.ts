import { useState } from "react";

import type { StoredWorkflow } from "@features/workflow/lib/workflow-storage";
import type {
  ChatKitStartScreenPrompt,
  ChatKitSupportedModel,
} from "@features/workflow/lib/workflow-storage.types";

export function useWorkflowMetadataState() {
  const [workflowName, setWorkflowName] = useState("New Workflow");
  const [workflowDescription, setWorkflowDescription] = useState("");
  const [currentWorkflowId, setCurrentWorkflowId] = useState<string | null>(
    null,
  );
  const [workflowVersions, setWorkflowVersions] = useState<
    StoredWorkflow["versions"]
  >([]);
  const [workflowTags, setWorkflowTags] = useState<string[]>(["draft"]);
  const [isWorkflowLoading, setIsWorkflowLoading] = useState(false);
  const [workflowLoadError, setWorkflowLoadError] = useState<string | null>(
    null,
  );
  const [isWorkflowPublic, setIsWorkflowPublic] = useState(false);
  const [workflowShareUrl, setWorkflowShareUrl] = useState<string | null>(null);
  const [chatkitStartScreenPrompts, setChatkitStartScreenPrompts] = useState<
    ChatKitStartScreenPrompt[] | null
  >(null);
  const [chatkitSupportedModels, setChatkitSupportedModels] = useState<
    ChatKitSupportedModel[] | null
  >(null);

  return {
    workflowName,
    setWorkflowName,
    workflowDescription,
    setWorkflowDescription,
    currentWorkflowId,
    setCurrentWorkflowId,
    workflowVersions,
    setWorkflowVersions,
    workflowTags,
    setWorkflowTags,
    isWorkflowLoading,
    setIsWorkflowLoading,
    workflowLoadError,
    setWorkflowLoadError,
    isWorkflowPublic,
    setIsWorkflowPublic,
    workflowShareUrl,
    setWorkflowShareUrl,
    chatkitStartScreenPrompts,
    setChatkitStartScreenPrompts,
    chatkitSupportedModels,
    setChatkitSupportedModels,
  };
}
