import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ExternalAgentsSection from "@features/account/components/external-agents-section";
import {
  disconnectExternalAgent,
  getExternalAgentLoginSession,
  getExternalAgents,
  refreshExternalAgents,
  startExternalAgentLogin,
  submitExternalAgentLoginInput,
} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  disconnectExternalAgent: vi.fn(),
  getExternalAgents: vi.fn(),
  refreshExternalAgents: vi.fn(),
  startExternalAgentLogin: vi.fn(),
  getExternalAgentLoginSession: vi.fn(),
  submitExternalAgentLoginInput: vi.fn(),
}));

const mockProviders = [
  {
    provider: "claude_code" as const,
    display_name: "Claude Code",
    state: "needs_login" as const,
    installed: true,
    authenticated: false,
    supports_oauth: true,
    resolved_version: "1.0.0",
    executable_path: "/data/claude/bin/claude",
    checked_at: "2026-03-31T10:00:00Z",
    last_auth_ok_at: null,
    detail: "OAuth login is required on the worker.",
    active_session_id: null,
  },
  {
    provider: "codex" as const,
    display_name: "Codex",
    state: "not_installed" as const,
    installed: false,
    authenticated: false,
    supports_oauth: true,
    resolved_version: null,
    executable_path: null,
    checked_at: "2026-03-31T10:00:00Z",
    last_auth_ok_at: null,
    detail: "Runtime not installed on the worker yet.",
    active_session_id: null,
  },
  {
    provider: "gemini" as const,
    display_name: "Gemini CLI",
    state: "needs_login" as const,
    installed: true,
    authenticated: false,
    supports_oauth: true,
    resolved_version: "0.36.0",
    executable_path: "/data/gemini/bin/gemini",
    checked_at: "2026-03-31T10:00:00Z",
    last_auth_ok_at: null,
    detail: "OAuth login is required on the worker.",
    active_session_id: null,
  },
];

describe("ExternalAgentsSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getExternalAgents).mockResolvedValue({
      providers: mockProviders,
    });
    vi.mocked(refreshExternalAgents).mockResolvedValue({
      providers: mockProviders,
    });
    vi.mocked(startExternalAgentLogin).mockResolvedValue({
      session_id: "session-1",
      provider: "codex",
      display_name: "Codex",
      state: "pending",
      created_at: "2026-03-31T10:00:00Z",
      updated_at: "2026-03-31T10:00:00Z",
      completed_at: null,
      auth_url: null,
      device_code: null,
      detail: "Preparing the worker-side OAuth flow.",
      recent_output: null,
      resolved_version: null,
      executable_path: null,
    });
    vi.mocked(disconnectExternalAgent).mockResolvedValue({
      provider: "gemini",
      display_name: "Gemini CLI",
      state: "checking",
      installed: true,
      authenticated: false,
      supports_oauth: true,
      resolved_version: "0.36.0",
      executable_path: "/data/gemini/bin/gemini",
      checked_at: "2026-03-31T10:00:00Z",
      last_auth_ok_at: null,
      detail: "Disconnecting worker auth state.",
      active_session_id: null,
    });
    vi.mocked(getExternalAgentLoginSession).mockResolvedValue({
      session_id: "session-1",
      provider: "codex",
      display_name: "Codex",
      state: "authenticated",
      created_at: "2026-03-31T10:00:00Z",
      updated_at: "2026-03-31T10:00:02Z",
      completed_at: "2026-03-31T10:00:02Z",
      auth_url: null,
      device_code: null,
      detail: "Worker authentication completed successfully.",
      recent_output: "Done",
      resolved_version: "0.30.0",
      executable_path: "/data/codex/bin/codex",
    });
    vi.mocked(submitExternalAgentLoginInput).mockResolvedValue({
      session_id: "session-claude",
      provider: "claude_code",
      display_name: "Claude Code",
      state: "awaiting_oauth",
      created_at: "2026-03-31T10:00:00Z",
      updated_at: "2026-03-31T10:00:02Z",
      completed_at: null,
      auth_url: "https://claude.ai",
      device_code: null,
      detail: "Auth code submitted to the worker. Waiting for completion.",
      recent_output: "Paste the code back into Claude Code",
      resolved_version: "2.1.89",
      executable_path: "/data/claude/bin/claude",
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("renders worker-scoped provider cards", async () => {
    render(<ExternalAgentsSection />);

    expect(screen.getByText("External Agents")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Claude Code")).toBeInTheDocument();
      expect(screen.getByText("Codex")).toBeInTheDocument();
      expect(screen.getByText("Gemini CLI")).toBeInTheDocument();
    });

    expect(screen.getByText(/scoped to this workspace/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Enable device code authorization for Codex/i),
    ).toBeInTheDocument();
  });

  it("hides the Codex device-auth reminder after the worker is ready", async () => {
    vi.mocked(getExternalAgents).mockResolvedValue({
      providers: [
        mockProviders[0],
        {
          ...mockProviders[1],
          state: "ready",
          installed: true,
          authenticated: true,
          resolved_version: "0.30.0",
          executable_path: "/data/codex/bin/codex",
          detail: "Worker is ready to run Codex.",
        },
        mockProviders[2],
      ],
    });
    vi.mocked(refreshExternalAgents).mockResolvedValue({
      providers: [
        mockProviders[0],
        {
          ...mockProviders[1],
          state: "ready",
          installed: true,
          authenticated: true,
          resolved_version: "0.30.0",
          executable_path: "/data/codex/bin/codex",
          detail: "Worker is ready to run Codex.",
        },
        mockProviders[2],
      ],
    });

    render(<ExternalAgentsSection />);

    await waitFor(() => {
      expect(screen.getByText("Codex")).toBeInTheDocument();
    });

    expect(
      screen.queryByText(/Enable device code authorization for Codex/i),
    ).not.toBeInTheDocument();
  });

  it("hides the Codex device-auth reminder once a login session is in progress", async () => {
    vi.mocked(getExternalAgents).mockResolvedValue({
      providers: [
        mockProviders[0],
        {
          ...mockProviders[1],
          state: "authenticating",
          installed: true,
          authenticated: false,
          resolved_version: "0.30.0",
          executable_path: "/data/codex/bin/codex",
          detail: "Waiting for browser-based sign-in.",
          active_session_id: "session-1",
        },
        mockProviders[2],
      ],
    });

    render(<ExternalAgentsSection />);

    await waitFor(() => {
      expect(screen.getByText("Codex")).toBeInTheDocument();
    });

    expect(
      screen.queryByText(/Enable device code authorization for Codex/i),
    ).not.toBeInTheDocument();
  });

  it("starts the worker login flow from Canvas", async () => {
    const user = userEvent.setup();
    render(<ExternalAgentsSection />);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /install and connect/i }),
      ).toBeVisible();
    });

    await user.click(
      screen.getAllByRole("button", { name: /install and connect/i })[0],
    );

    await waitFor(() => {
      expect(startExternalAgentLogin).toHaveBeenCalledWith("codex");
    });
  });

  it("submits a Claude auth code back to the worker session", async () => {
    const user = userEvent.setup();
    vi.mocked(getExternalAgents).mockResolvedValue({
      providers: [
        {
          ...mockProviders[0],
          active_session_id: "session-claude",
        },
        mockProviders[1],
        mockProviders[2],
      ],
    });
    vi.mocked(refreshExternalAgents).mockResolvedValue({
      providers: [
        {
          ...mockProviders[0],
          active_session_id: "session-claude",
        },
        mockProviders[1],
        mockProviders[2],
      ],
    });
    vi.mocked(getExternalAgentLoginSession).mockResolvedValue({
      session_id: "session-claude",
      provider: "claude_code",
      display_name: "Claude Code",
      state: "awaiting_oauth",
      created_at: "2026-03-31T10:00:00Z",
      updated_at: "2026-03-31T10:00:02Z",
      completed_at: null,
      auth_url: "https://claude.ai",
      device_code: null,
      detail: "Complete the browser sign-in.",
      recent_output: "Paste the code back into Claude Code",
      resolved_version: "2.1.89",
      executable_path: "/data/claude/bin/claude",
    });

    render(<ExternalAgentsSection />);

    const input = await screen.findByPlaceholderText(
      /paste claude auth code or redirect url/i,
    );
    await user.type(input, "ABCD-1234");
    await user.click(screen.getByRole("button", { name: /submit code/i }));

    await waitFor(() => {
      expect(submitExternalAgentLoginInput).toHaveBeenCalledWith(
        "session-claude",
        {
          input_text: "ABCD-1234",
        },
      );
    });
  });

  it("explains the accepted Claude auth input formats", async () => {
    vi.mocked(getExternalAgents).mockResolvedValue({
      providers: [
        {
          ...mockProviders[0],
          active_session_id: "session-claude",
        },
        mockProviders[1],
        mockProviders[2],
      ],
    });
    vi.mocked(refreshExternalAgents).mockResolvedValue({
      providers: [
        {
          ...mockProviders[0],
          active_session_id: "session-claude",
        },
        mockProviders[1],
        mockProviders[2],
      ],
    });
    vi.mocked(getExternalAgentLoginSession).mockResolvedValue({
      session_id: "session-claude",
      provider: "claude_code",
      display_name: "Claude Code",
      state: "awaiting_oauth",
      created_at: "2026-03-31T10:00:00Z",
      updated_at: "2026-03-31T10:00:02Z",
      completed_at: null,
      auth_url: "https://claude.ai",
      device_code: null,
      detail: "Complete the browser sign-in.",
      recent_output: "Paste the code back into Claude Code",
      resolved_version: "2.1.89",
      executable_path: "/data/claude/bin/claude",
    });

    render(<ExternalAgentsSection />);

    expect(
      await screen.findByText(/accepts the final redirect url/i),
    ).toBeInTheDocument();
  });

  it("hides the raw Claude sign-in URL behind the action link", async () => {
    const authUrl =
      "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=user%3Ainference";

    vi.mocked(getExternalAgents).mockResolvedValue({
      providers: [
        {
          ...mockProviders[0],
          active_session_id: "session-claude",
        },
        mockProviders[1],
      ],
    });
    vi.mocked(refreshExternalAgents).mockResolvedValue({
      providers: [
        {
          ...mockProviders[0],
          active_session_id: "session-claude",
        },
        mockProviders[1],
      ],
    });
    vi.mocked(getExternalAgentLoginSession).mockResolvedValue({
      session_id: "session-claude",
      provider: "claude_code",
      display_name: "Claude Code",
      state: "awaiting_oauth",
      created_at: "2026-03-31T10:00:00Z",
      updated_at: "2026-03-31T10:00:02Z",
      completed_at: null,
      auth_url: authUrl,
      device_code: null,
      detail: "Complete the browser sign-in.",
      recent_output: "Paste the code back into Claude Code",
      resolved_version: "2.1.89",
      executable_path: "/data/claude/bin/claude",
    });

    render(<ExternalAgentsSection />);

    expect(
      await screen.findByRole("link", { name: /open sign-in/i }),
    ).toHaveAttribute("href", authUrl);
    expect(screen.queryByText(authUrl)).not.toBeInTheDocument();
  });

  it("submits a Gemini verification code back to the worker session", async () => {
    const user = userEvent.setup();
    vi.mocked(getExternalAgents).mockResolvedValue({
      providers: [
        mockProviders[0],
        mockProviders[1],
        { ...mockProviders[2], active_session_id: "session-gemini" },
      ],
    });
    vi.mocked(refreshExternalAgents).mockResolvedValue({
      providers: [
        mockProviders[0],
        mockProviders[1],
        { ...mockProviders[2], active_session_id: "session-gemini" },
      ],
    });
    vi.mocked(getExternalAgentLoginSession).mockResolvedValue({
      session_id: "session-gemini",
      provider: "gemini",
      display_name: "Gemini CLI",
      state: "awaiting_oauth",
      created_at: "2026-03-31T10:00:00Z",
      updated_at: "2026-03-31T10:00:02Z",
      completed_at: null,
      auth_url: "https://accounts.google.com/o/oauth2/v2/auth",
      device_code: "ABCD-1234",
      detail: "Complete the browser sign-in.",
      recent_output: "Enter the verification code shown in the terminal.",
      resolved_version: "0.36.0",
      executable_path: "/data/gemini/bin/gemini",
    });

    render(<ExternalAgentsSection />);

    const input = await screen.findByPlaceholderText(
      /paste gemini verification code/i,
    );
    await user.type(input, "ABCD-1234");
    await user.click(screen.getByRole("button", { name: /submit code/i }));

    await waitFor(() => {
      expect(submitExternalAgentLoginInput).toHaveBeenCalledWith(
        "session-gemini",
        {
          input_text: "ABCD-1234",
        },
      );
    });
  });

  it("renders Disconnect for ready providers and calls the disconnect route", async () => {
    const user = userEvent.setup();
    vi.mocked(getExternalAgents).mockResolvedValue({
      providers: [
        mockProviders[0],
        mockProviders[1],
        {
          ...mockProviders[2],
          state: "ready",
          authenticated: true,
          detail: "Worker is ready to run Gemini.",
        },
      ],
    });
    vi.mocked(refreshExternalAgents).mockResolvedValue({
      providers: [
        mockProviders[0],
        mockProviders[1],
        {
          ...mockProviders[2],
          state: "ready",
          authenticated: true,
          detail: "Worker is ready to run Gemini.",
        },
      ],
    });

    render(<ExternalAgentsSection />);

    const disconnectButton = await screen.findByRole("button", {
      name: /disconnect/i,
    });
    await user.click(disconnectButton);

    await waitFor(() => {
      expect(disconnectExternalAgent).toHaveBeenCalledWith("gemini");
    });
  });
});
