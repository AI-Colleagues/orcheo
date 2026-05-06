# Orcheo

[![CI](https://github.com/AI-Colleagues/orcheo/actions/workflows/ci.yml/badge.svg?event=push)](https://github.com/AI-Colleagues/orcheo/actions/workflows/ci.yml?query=branch%3Amain)
[![Coverage](https://coverage-badge.samuelcolvin.workers.dev/AI-Colleagues/orcheo.svg)](https://coverage-badge.samuelcolvin.workers.dev/redirect/AI-Colleagues/orcheo)
[![PyPI - Core](https://img.shields.io/pypi/v/orcheo.svg?logo=python&label=core)](https://pypi.org/project/orcheo/)
[![PyPI - Backend](https://img.shields.io/pypi/v/orcheo-backend.svg?logo=python&label=backend)](https://pypi.org/project/orcheo-backend/)
[![PyPI - SDK](https://img.shields.io/pypi/v/orcheo-sdk.svg?logo=python&label=sdk)](https://pypi.org/project/orcheo-sdk/)
[![PyPI - Agentensor](https://img.shields.io/pypi/v/agentensor.svg?logo=python&label=agentensor)](https://pypi.org/project/agentensor/)
[![npm - Canvas](https://img.shields.io/npm/v/orcheo-canvas.svg?logo=npm&label=canvas)](https://www.npmjs.com/package/orcheo-canvas)

Orcheo is a workflow orchestration platform designed for vibe coding — AI coding agents like Claude Code can start services, build workflows, and deploy them for you automatically.

!!! note
    This project is currently in Beta. Expect breaking changes as we iterate rapidly towards 1.0.

## Why Orcheo?

- **Vibe-coding-first**: Already using Claude Code, Codex CLI, or Cursor? You **don't** need to learn Orcheo. Install the [agent skill](https://github.com/AI-Colleagues/agent-skills) and let your AI agent handle setup, workflow creation, and deployment.
- **Python-native**: Workflows are Python code powered by LangGraph — no proprietary DSL to learn.
- **CLI-first**: Manage everything via the `orcheo` CLI (or `horcheo` for human-readable output) — install, configure, deploy, and run workflows from the terminal. The GUI is optional.

## Quick Start

Use the installation path that matches your setup:

> Prerequisite: Docker Desktop/Engine must be installed to run the stack (`orcheo install --start-stack`).

=== "macOS / Linux"

    ```bash
    bash <(curl -fsSL https://ai-colleagues.com/install.sh)
    ```

=== "Windows PowerShell"

    ```powershell
    irm https://ai-colleagues.com/install.ps1 | iex
    ```

=== "SDK"

    ```bash
    uv tool install -U orcheo-sdk
    orcheo install
    ```

=== "macOS/Linux (non-interactive)"

    ```bash
    curl -fsSL https://ai-colleagues.com/install.sh | bash -s -- --yes --start-stack
    ```

=== "Upgrade"

    ```bash
    orcheo install upgrade --yes
    ```

`orcheo install` syncs stack assets into `~/.orcheo/stack` (or
`ORCHEO_STACK_DIR`), updates `.env` with setup-selected values, and can start
the stack with Docker Compose. Setup prompts for
`VITE_ORCHEO_CHATKIT_DOMAIN_KEY`; you can skip and continue, but ChatKit UI
features remain disabled until a valid key is configured.

The Agent Skill flow remains a strong option for workflow authoring with coding agents.

!!! info "Subscription Required"
    Most AI coding agents (Claude Code, Codex CLI, Cursor) require a paid subscription. Free alternatives may exist but have not been tested with Orcheo.

### 1. Install the Orcheo Agent Skill

Add the [Orcheo agent skill](https://github.com/AI-Colleagues/agent-skills) to your AI coding agent (Claude Code, Cursor, etc.) by following the installation instructions in the repo.

### 2. Let Your Agent Do the Work

Once installed, simply ask your agent to:

- **Set up Orcheo**: "Set up Orcheo for local development"
- **Create workflows**: "Create a workflow that monitors RSS feeds and sends Slack notifications"
- **Deploy workflows**: "Deploy and schedule my workflow to run every hour"

Your AI agent will automatically:

- Install dependencies
- Start the backend server
- Create and configure workflows
- Handle authentication and deployment

That's it! Your agent handles the complexity while you focus on describing what you want your workflows to do.

## Guides

- **[Manual Setup Guide](manual_setup.md)** — Installation and configuration
- **[Canvas](canvas.md)** — Workflow monitor, default-config editor, and Credential Vault manager
- **[Auth0 IdP Setup](auth0_idp_setup.md)** — Configure Auth0 OAuth/OIDC for the Docker stack
- **[MCP Integration](mcp_integration.md)** — Connect AI assistants to Orcheo
- **[Workflow Config Annotations](workflow_config_annotations.md)** — Declare typed configurable fields with a companion schema file
- **[Conversational Search](examples/conversational_search.md)** — Step-by-step demos from basic RAG to production-ready search
- **[Evaluation](examples/evaluation.md)** — QReCC and MultiDoc2Dial benchmark workflows

## Reference

- **[CLI Reference](cli_reference.md)** — Command reference for the `orcheo` CLI
- **[SDK Reference](sdk_reference.md)** — Python SDK for programmatic workflow execution
- **[Authentication Guide](authentication_guide.md)** — Service tokens, OAuth, and JWT configuration
- **[Environment Variables](environment_variables.md)** — Complete configuration reference

## For Developers

- **[Developer Guide](developer_guide.md)** — Repository layout, development environment, and testing
- **[Deployment Guide](deployment.md)** — Docker Compose and PostgreSQL deployment recipes
- **[Plugin Tutorial](plugin_tutorial.md)** — Step-by-step guide: scaffold, register, test, and distribute a plugin
- **[Plugin Reference](custom_nodes_and_tools.md)** — Full reference for all plugin authoring concepts and the registration API

## Contributing

We welcome contributions from the community:

- **Share your extensions**: Plugins, workflows, and integrations that extend Orcheo's capabilities. See the [Plugin Tutorial](plugin_tutorial.md) to get started and the [Plugin Reference](custom_nodes_and_tools.md) for the full API.
- **How to contribute**: Open an [issue](https://github.com/AI-Colleagues/orcheo/issues), submit a [pull request](https://github.com/AI-Colleagues/orcheo/pulls), or start a [discussion](https://github.com/AI-Colleagues/orcheo/discussions). You can also publish and share your extensions independently for others to install.
