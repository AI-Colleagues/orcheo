import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { getAuthTokens, setAuthTokens } from "../lib/auth-session";
import RequireAuth from "./require-auth";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("VITE_ORCHEO_AUTH_DISABLED", "false");
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetAllMocks();
});

const renderGate = () =>
  render(
    <MemoryRouter initialEntries={["/protected"]}>
      <Routes>
        <Route element={<RequireAuth />}>
          <Route path="/protected" element={<p>Protected content</p>} />
        </Route>
        <Route path="/login" element={<p>Login page</p>} />
      </Routes>
    </MemoryRouter>,
  );

it("keeps the session and offers an explicit retry during an outage", async () => {
  setAuthTokens({
    accessToken: "expired",
    refreshToken: "r-old",
    expiresAt: Date.now() - 1000,
  });
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
  renderGate();

  expect(
    await screen.findByText(/sign-in is temporarily unavailable/i),
  ).toBeTruthy();
  expect(screen.queryByText("Login page")).toBeNull();
  expect(getAuthTokens()?.refreshToken).toBe("r-old");
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fetchMock.mockResolvedValueOnce(
    new Response(
      JSON.stringify({
        access_token: "new",
        refresh_token: "r-new",
        expires_in: 900,
      }),
      { status: 200 },
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("Protected content")).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("redirects to login for a definitively rejected refresh token", async () => {
  setAuthTokens({
    accessToken: "expired",
    refreshToken: "r-old",
    expiresAt: Date.now() - 1000,
  });
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
  renderGate();
  expect(await screen.findByText("Login page")).toBeTruthy();
  expect(getAuthTokens()).toBeNull();
});
