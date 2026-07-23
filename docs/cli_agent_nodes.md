# CLI Coding-Agent Nodes

Orcheo can drive a **host-installed, pre-authenticated CLI coding agent**
(Codex, Claude Code, Antigravity) as a non-interactive workflow step. Each node
resolves the provider binary on `PATH`, runs it once with your prompt, and
returns its captured output.

| Node | Binary | Provider CLI |
| --- | --- | --- |
| `CodexNode` | `codex` | OpenAI Codex CLI |
| `ClaudeCodeNode` | `claude` | Claude Code |
| `AntigravityNode` | `agy` | Antigravity CLI |

!!! danger "Trusted workflows only — runs unsandboxed with the worker's privileges"

    These nodes invoke their CLI with **all safety rails disabled**
    (`--dangerously-bypass-approvals-and-sandbox` /
    `--dangerously-skip-permissions`). The agent executes arbitrary commands
    with the **worker process's own host privileges** — no sandbox, no approval
    prompts.

    - They register as **restricted** (`NodeMetadata(restricted=True)`), so
      [restricted-mode](sandboxed_custom_workflows.md) (untrusted-author)
      ingestion rejects them. They are only available to trusted, first-party
      workflows.
    - `restricted=True` only stops an untrusted *author* from registering the
      node. It does **not** stop a trusted workflow from piping untrusted
      *data* into it at runtime. `prompt`, `system_prompt`, and
      `working_directory` are ordinary template-interpolated fields
      (`{{...}}`), so interpolating a webhook body, an inbound message, or
      upstream tool output into them hands an unattended coding agent
      attacker-controlled instructions.
    - **Only populate these fields from trusted, workflow-controlled values.**
      Never feed unsanitized external input into a CLI agent node.

## Host setup

The node does **not** install or authenticate anything. Before a workflow can
use it, an operator must install and log in to the provider CLI on **every
worker host** that runs the workflow:

```bash
# Claude Code — install, then create a long-lived token
npm install -g @anthropic-ai/claude-code
claude setup-token

# OpenAI Codex — install, then authenticate
npm install -g @openai/codex
codex login

# Antigravity — install and authenticate per the provider's instructions
```

No credentials are materialized, probed, or injected by the node — it only
resolves the executable on `PATH` and runs it. If the binary is missing the node
raises before spawning any subprocess:

```text
'claude' was not found on PATH. Install and authenticate the claude CLI on this
host before running node '<name>'.
```

## Configuration

All three nodes share the same fields (from `CLIAgentNode`):

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `prompt` | `str` | *(required)* | Task instructions sent to the agent. |
| `system_prompt` | `str \| None` | `None` | Optional system instructions. Providers without a dedicated flag fold this into the prompt as `System instructions:\n…\n\nTask:\n…`. |
| `working_directory` | `str \| None` | `None` | Directory the CLI runs in. Defaults to the worker's cwd; must exist on the host or the node raises. |
| `timeout_seconds` | `int` | `600` | Max time to wait for the process. On timeout the whole process group is terminated (`SIGTERM`, then `SIGKILL`). |
| `raise_on_error` | `bool` | `True` | Raise if the CLI exits non-zero or times out. Set `False` to surface the failure in the output instead. |

## Output contract

On success the node returns:

| Key | Type | Description |
| --- | --- | --- |
| `output` | `str` | Captured stdout. |
| `stderr` | `str` | Captured stderr. |
| `exit_code` | `int \| None` | Process exit code (`None` if it timed out before exiting). |
| `timed_out` | `bool` | Whether the run hit `timeout_seconds`. |
| `duration_seconds` | `float` | Wall-clock run time. |

## Error contract

- **Missing binary** or **non-existent `working_directory`** → `RuntimeError`
  before any subprocess is spawned.
- With `raise_on_error=True` (default), a **non-zero exit** or **timeout**
  raises `RuntimeError` carrying the captured `stderr` (or `stdout`).
- With `raise_on_error=False`, the same failures are reported through the output
  fields (`exit_code`, `timed_out`, `stderr`) without raising.
- If the workflow run is **cancelled**, the node terminates the CLI process
  group before propagating the cancellation, so the unsandboxed agent cannot
  keep mutating the working tree after the run is reported cancelled.

## Example

```python
from orcheo.graph import StateGraph, START, END
from orcheo.graph.state import State
from orcheo.nodes import ClaudeCodeNode


async def orcheo_workflow() -> StateGraph:
    graph = StateGraph(State)
    graph.add_node(
        "fix_lint",
        ClaudeCodeNode(
            name="fix_lint",
            # Trusted, workflow-controlled instruction — NOT external input.
            prompt="Run the linter and fix every reported issue.",
            system_prompt="Only touch files under src/. Keep changes minimal.",
            working_directory="/srv/checkouts/my-repo",
            timeout_seconds=900,
        ),
    )
    graph.add_edge(START, "fix_lint")
    graph.add_edge("fix_lint", END)
    return graph
```

Swap `ClaudeCodeNode` for `CodexNode` or `AntigravityNode` — the fields are
identical.
