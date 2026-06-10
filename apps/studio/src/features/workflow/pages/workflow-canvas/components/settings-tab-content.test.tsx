import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SettingsTabContent } from "./settings-tab-content";

afterEach(() => {
  cleanup();
});

const baseProps = {
  workflowId: "wf-1",
  workflowName: "Listener Workflow",
  workflowDescription: "A workflow with listeners.",
  workflowTags: ["bot", "listener"],
  missingCredentials: [],
  onWorkflowNameChange: vi.fn(),
  onWorkflowDescriptionChange: vi.fn(),
  onTagsChange: vi.fn(),
  onSaveWorkflowDetails: vi.fn(async () => undefined),
  isSavingWorkflowDetails: false,
  workflowVersions: [],
  onRestoreVersion: vi.fn(),
  listeners: [
    {
      subscription_id: "sub-1",
      node_name: "telegram_listener",
      platform: "telegram" as const,
      status: "active" as const,
      bot_identity_key: "telegram:primary",
      assigned_runtime: "listener-runtime-1",
      lease_expires_at: null,
      last_event_at: "2026-03-11T10:00:00Z",
      last_error: null,
      runtime_status: "healthy" as const,
      runtime_detail: null,
      last_polled_at: "2026-03-11T10:01:00Z",
      consecutive_failures: 0,
    },
    {
      subscription_id: "sub-2",
      node_name: "discord_listener",
      platform: "discord" as const,
      status: "blocked" as const,
      bot_identity_key: "discord:primary",
      assigned_runtime: null,
      lease_expires_at: null,
      last_event_at: null,
      last_error: "Missing credential: discord_token",
      runtime_status: "stopped" as const,
      runtime_detail: null,
      last_polled_at: null,
      consecutive_failures: 0,
    },
  ],
  listenerMetrics: {
    workflow_id: "wf-1",
    total_subscriptions: 2,
    active_subscriptions: 1,
    blocked_subscriptions: 1,
    paused_subscriptions: 0,
    disabled_subscriptions: 0,
    error_subscriptions: 0,
    healthy_runtimes: 1,
    reconnecting_runtimes: 0,
    stalled_listeners: 0,
    dispatch_failures: 0,
    by_platform: [],
    alerts: [],
  },
  isListenersLoading: false,
  isListenersRefreshing: false,
  activeListenerSubscriptionId: null,
  onRefreshListeners: vi.fn(async () => undefined),
  onPauseListener: vi.fn(async () => undefined),
  onResumeListener: vi.fn(async () => undefined),
};

describe("SettingsTabContent listener controls", () => {
  it("shows a save-first message when the workflow is not persisted", () => {
    render(
      <SettingsTabContent
        {...baseProps}
        workflowId={null}
        listeners={[]}
        listenerMetrics={null}
      />,
    );

    expect(screen.getByText(/save the workflow first/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /save details/i }),
    ).toBeDisabled();
  });

  it("forwards the explicit workflow-details save action", async () => {
    const user = userEvent.setup();
    const onSaveWorkflowDetails = vi.fn(async () => undefined);

    render(
      <SettingsTabContent
        {...baseProps}
        onSaveWorkflowDetails={onSaveWorkflowDetails}
      />,
    );

    await user.click(screen.getByRole("button", { name: /save details/i }));

    expect(onSaveWorkflowDetails).toHaveBeenCalledTimes(1);
  });

  it("shows missing credentials on the details page", () => {
    render(
      <SettingsTabContent
        {...baseProps}
        missingCredentials={["mdb_connection_string"]}
      />,
    );

    expect(screen.getByText(/missing credentials/i)).toBeInTheDocument();
    expect(screen.getByText("mdb_connection_string")).toBeInTheDocument();
  });

  it("renders listener actions and forwards pause/resume callbacks", async () => {
    const user = userEvent.setup();
    const onPauseListener = vi.fn(async () => undefined);
    const onResumeListener = vi.fn(async () => undefined);

    render(
      <SettingsTabContent
        {...baseProps}
        onPauseListener={onPauseListener}
        onResumeListener={onResumeListener}
      />,
    );

    expect(
      screen.getByRole("heading", { name: /listener control/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("telegram_listener")).toBeInTheDocument();
    expect(screen.getByText("discord_listener")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^pause$/i }));
    await user.click(screen.getByRole("button", { name: /^resume$/i }));

    expect(onPauseListener).toHaveBeenCalledWith("sub-1");
    expect(onResumeListener).toHaveBeenCalledWith("sub-2");
  });
});
