import { useEffect, useState } from "react";

import { toast } from "@/hooks/use-toast";
import {
  fetchWorkflowListenerMetrics,
  fetchWorkflowListeners,
  pauseWorkflowListener,
  resumeWorkflowListener,
} from "@features/workflow/lib/workflow-storage-api";
import type {
  WorkflowListenerHealth,
  WorkflowListenerMetricsResponse,
} from "@features/workflow/lib/workflow-storage.types";

type UseWorkflowListenersArgs = {
  routeWorkflowId?: string;
  currentWorkflowId: string | null;
  workflowVersionCount: number;
  actor: string;
  enabled?: boolean;
};

type ListenerAction = "pause" | "resume";

export function useWorkflowListeners({
  routeWorkflowId,
  currentWorkflowId,
  workflowVersionCount,
  actor,
  enabled = true,
}: UseWorkflowListenersArgs) {
  const workflowId = currentWorkflowId ?? routeWorkflowId ?? null;
  const [listeners, setListeners] = useState<WorkflowListenerHealth[]>([]);
  const [metrics, setMetrics] =
    useState<WorkflowListenerMetricsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [activeSubscriptionId, setActiveSubscriptionId] = useState<
    string | null
  >(null);

  useEffect(() => {
    let isActive = true;

    if (!enabled || !workflowId) {
      setListeners([]);
      setMetrics(null);
      setIsLoading(false);
      setIsRefreshing(false);
      setActiveSubscriptionId(null);
      return () => {
        isActive = false;
      };
    }

    const load = async () => {
      if (!isActive) {
        return;
      }

      setIsLoading(true);
      try {
        const [nextListeners, nextMetrics] = await Promise.all([
          fetchWorkflowListeners(workflowId),
          fetchWorkflowListenerMetrics(workflowId),
        ]);
        if (!isActive) {
          return;
        }
        setListeners(nextListeners);
        setMetrics(nextMetrics ?? null);
      } catch (error) {
        if (!isActive) {
          return;
        }
        console.error("Failed to load workflow listeners", error);
        toast({
          title: "Unable to load listeners",
          description:
            error instanceof Error
              ? error.message
              : "An unexpected error occurred while loading listeners.",
          variant: "destructive",
        });
      } finally {
        if (isActive) {
          setIsLoading(false);
        }
      }
    };

    void load();

    return () => {
      isActive = false;
    };
  }, [enabled, workflowId, workflowVersionCount]);

  const refreshListeners = async () => {
    if (!enabled || !workflowId) {
      return;
    }

    setIsRefreshing(true);
    try {
      const [nextListeners, nextMetrics] = await Promise.all([
        fetchWorkflowListeners(workflowId),
        fetchWorkflowListenerMetrics(workflowId),
      ]);
      setListeners(nextListeners);
      setMetrics(nextMetrics ?? null);
    } catch (error) {
      console.error("Failed to refresh workflow listeners", error);
      toast({
        title: "Unable to refresh listeners",
        description:
          error instanceof Error
            ? error.message
            : "An unexpected error occurred while refreshing listeners.",
        variant: "destructive",
      });
    } finally {
      setIsRefreshing(false);
    }
  };

  const updateListenerStatus = async (
    subscriptionId: string,
    action: ListenerAction,
  ) => {
    if (!workflowId) {
      return;
    }

    setActiveSubscriptionId(subscriptionId);
    try {
      const updated =
        action === "pause"
          ? await pauseWorkflowListener(workflowId, subscriptionId, actor)
          : await resumeWorkflowListener(workflowId, subscriptionId, actor);
      setListeners((current) =>
        current.map((item) =>
          item.subscription_id === subscriptionId ? updated : item,
        ),
      );
      toast({
        title: action === "pause" ? "Listener paused" : "Listener resumed",
        description: `${updated.node_name} is now ${updated.status}.`,
      });
      await refreshListeners();
    } catch (error) {
      console.error(`Failed to ${action} workflow listener`, error);
      toast({
        title:
          action === "pause"
            ? "Unable to pause listener"
            : "Unable to resume listener",
        description:
          error instanceof Error
            ? error.message
            : "An unexpected error occurred while updating the listener.",
        variant: "destructive",
      });
    } finally {
      setActiveSubscriptionId(null);
    }
  };

  return {
    workflowId,
    listeners,
    metrics,
    isLoading,
    isRefreshing,
    activeSubscriptionId,
    refreshListeners,
    pauseListener: (subscriptionId: string) =>
      updateListenerStatus(subscriptionId, "pause"),
    resumeListener: (subscriptionId: string) =>
      updateListenerStatus(subscriptionId, "resume"),
  };
}
