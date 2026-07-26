const form = document.querySelector("#message-form");
const result = document.querySelector("#result");

const createIdempotencyKey = () => crypto.randomUUID();

const pollRun = async (handle) => {
  while (true) {
    const response = await fetch(
      `/__orcheo/runs/${encodeURIComponent(handle)}`,
    );
    if (!response.ok) throw new Error("Unable to read the workflow run.");
    const run = await response.json();
    if (run.status === "completed") return run.output;
    if (["failed", "cancelled"].includes(run.status)) {
      throw new Error(run.error || "Workflow execution failed.");
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
};

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const binding = event.submitter?.dataset.binding || "greet";
  const name = new FormData(form).get("name");
  result.textContent = "Running…";
  try {
    const response = await fetch(
      `/__orcheo/workflows/${encodeURIComponent(binding)}/runs`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": createIdempotencyKey(),
        },
        body: JSON.stringify({ name }),
      },
    );
    if (!response.ok) throw new Error("Unable to start the workflow.");
    const accepted = await response.json();
    const output = await pollRun(accepted.handle);
    const message =
      output?.final_state?.structured_response?.greeting ||
      output?.final_state?.structured_response?.farewell;
    result.textContent = message || "Workflow completed.";
  } catch (error) {
    result.textContent =
      error instanceof Error ? error.message : "Workflow request failed.";
  }
});
