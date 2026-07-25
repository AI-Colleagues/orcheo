const form = document.querySelector("#greeting-form");
const nameInput = document.querySelector("#name");
const submitButton = form?.querySelector("button[type='submit']");
const status = document.querySelector("#status");
const result = document.querySelector("#result");
const greeting = document.querySelector("#greeting");

const POLL_INTERVAL_MS = 750;
const RUN_TIMEOUT_MS = 60_000;
const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

function setStatus(message, state) {
  if (!status) return;
  status.textContent = message;
  status.dataset.state = state;
}

function createIdempotencyKey() {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function readJson(response) {
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      payload?.detail?.message ?? payload?.detail ?? "The request was rejected.";
    throw new Error(typeof message === "string" ? message : "Request failed.");
  }
  return payload;
}

function wait(duration) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, duration);
  });
}

async function waitForRun(handle) {
  const deadline = Date.now() + RUN_TIMEOUT_MS;

  while (Date.now() < deadline) {
    const response = await fetch(
      `/__orcheo/runs/${encodeURIComponent(handle)}`,
      {
        headers: { Accept: "application/json" },
        cache: "no-store",
      },
    );
    const run = await readJson(response);

    if (TERMINAL_STATUSES.has(run.status)) {
      return run;
    }

    setStatus(
      run.status === "running"
        ? "The workflow is running…"
        : "The workflow accepted the request…",
      "working",
    );
    await wait(POLL_INTERVAL_MS);
  }

  throw new Error("The workflow did not finish within 60 seconds.");
}

function greetingFrom(run) {
  return (
    run.output?.greeting ??
    run.output?.final_state?.structured_response?.greeting ??
    run.output?.final_state?.node_results?.create_greeting?.greeting
  );
}

async function runGreeting(name) {
  const response = await fetch("/__orcheo/workflows/greet/runs", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": createIdempotencyKey(),
    },
    body: JSON.stringify({ name }),
  });
  const accepted = await readJson(response);

  if (!accepted?.handle) {
    throw new Error("The workflow response did not include a run handle.");
  }

  return waitForRun(accepted.handle);
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = nameInput?.value.trim() ?? "";

  if (!name) {
    setStatus("Enter a name before running the workflow.", "error");
    nameInput?.focus();
    return;
  }

  if (submitButton) submitButton.disabled = true;
  if (result) result.hidden = true;
  setStatus("Sending the request to the greet binding…", "working");

  try {
    const run = await runGreeting(name);
    if (run.status !== "completed") {
      throw new Error(run.error || `The workflow ${run.status}.`);
    }

    const message = greetingFrom(run);
    if (!message) {
      throw new Error("The workflow completed without a projected greeting.");
    }

    if (greeting) greeting.textContent = message;
    if (result) result.hidden = false;
    setStatus("Workflow completed successfully.", "success");
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "The workflow request failed.";
    setStatus(message, "error");
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
});
