# Orcheo

[![CI](https://github.com/AI-Colleagues/orcheo/actions/workflows/ci.yml/badge.svg?event=push)](https://github.com/AI-Colleagues/orcheo/actions/workflows/ci.yml?query=branch%3Amain)
[![Coverage](https://coverage-badge.samuelcolvin.workers.dev/AI-Colleagues/orcheo.svg)](https://coverage-badge.samuelcolvin.workers.dev/redirect/AI-Colleagues/orcheo)
[![PyPI - Core](https://img.shields.io/pypi/v/orcheo.svg?logo=python&label=core)](https://pypi.org/project/orcheo/)
[![PyPI - Backend](https://img.shields.io/pypi/v/orcheo-backend.svg?logo=python&label=backend)](https://pypi.org/project/orcheo-backend/)
[![PyPI - SDK](https://img.shields.io/pypi/v/orcheo-sdk.svg?logo=python&label=sdk)](https://pypi.org/project/orcheo-sdk/)
[![PyPI - Agentensor](https://img.shields.io/pypi/v/agentensor.svg?logo=python&label=agentensor)](https://pypi.org/project/agentensor/)
[![npm - Canvas](https://img.shields.io/npm/v/orcheo-canvas.svg?logo=npm&label=canvas)](https://www.npmjs.com/package/orcheo-canvas)
[![GHCR - Stack](https://img.shields.io/badge/dynamic/xml?url=https%3A%2F%2Fghcr-badge.egpl.dev%2Fai-colleagues%2Forcheo-stack%2Flatest_tag%3Fignore%3Dlatest&query=%2F%2F*%5Blocal-name()%3D%27g%27%5D%5Blast()%5D%2F*%5Blocal-name()%3D%27text%27%5D%5Blast()%5D&logo=docker&label=stack)](https://github.com/AI-Colleagues/orcheo/pkgs/container/orcheo-stack)
[![GHCR - Canvas](https://img.shields.io/badge/dynamic/xml?url=https%3A%2F%2Fghcr-badge.egpl.dev%2Fai-colleagues%2Forcheo-canvas%2Flatest_tag%3Fignore%3Dlatest&query=%2F%2F*%5Blocal-name()%3D%27g%27%5D%5Blast()%5D%2F*%5Blocal-name()%3D%27text%27%5D%5Blast()%5D&logo=docker&label=canvas)](https://github.com/AI-Colleagues/orcheo/pkgs/container/orcheo-canvas)
[![Documentation](https://readthedocs.org/projects/orcheo/badge/?version=latest)](https://orcheo.readthedocs.io/en/latest/)

Orcheo is a workflow orchestration platform designed for vibe coding — AI coding agents like Claude Code can start services, build workflows, and deploy them for you automatically. Read the [full documentation](https://orcheo.readthedocs.io/en/latest/) for guides, API reference, and examples.

> **Note:** This project is currently in Beta. Expect breaking changes as we iterate rapidly towards 1.0.

> **SIGIR Reviewers:** See the **[Conversational Search Examples](https://orcheo.readthedocs.io/en/latest/examples/conversational_search/)** for step-by-step demos from basic RAG to production-ready search.

## Why Orcheo?

- **Vibe-coding-first**: Already using Claude Code, Codex CLI, or Cursor? You **don't** need to learn Orcheo. Install the [agent skill](https://github.com/AI-Colleagues/agent-skills) and let your AI agent handle setup, workflow creation, and deployment.
- **Python-native**: Workflows are Python code powered by LangGraph — no proprietary DSL to learn.
- **CLI-first**: Run headless in production; the UI is optional.

## Quick Start

Use the installation path that matches your setup:

> Prerequisite: Docker Desktop/Engine must be installed to run the stack (`orcheo install --start-stack`).

<details open>
<summary>macOS / Linux</summary>

```bash
bash <(curl -fsSL https://ai-colleagues.com/install.sh)
```

</details>

<details>
<summary>Windows PowerShell</summary>

```powershell
irm https://ai-colleagues.com/install.ps1 | iex
```

</details>

<details>
<summary>SDK</summary>

```bash
uv tool install -U orcheo-sdk
orcheo install
```

</details>

<details>
<summary>macOS/Linux (non-interactive)</summary>

```bash
curl -fsSL https://ai-colleagues.com/install.sh | bash -s -- --yes --start-stack
```

</details>

<details>
<summary>Upgrade</summary>

```bash
orcheo install upgrade --yes
```

</details>

`orcheo install` syncs Docker stack assets into `~/.orcheo/stack` (or
`ORCHEO_STACK_DIR`), updates `.env` with setup-selected values, and can start the
stack with Docker Compose. Setup will prompt for
`VITE_ORCHEO_CHATKIT_DOMAIN_KEY`; you can skip it and continue, but ChatKit UI
features will stay disabled until you set a valid key.

The fastest way to get started with workflow building is still the **Agent Skill** approach.

> **Note:** Most AI coding agents (Claude Code, Codex CLI, Cursor) require a paid subscription. Free alternatives may exist but have not been tested with Orcheo.

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

- **[Manual Setup Guide](https://orcheo.readthedocs.io/en/latest/manual_setup/)** — Installation, CLI reference, authentication, and Canvas setup
- **[Conversational Search Examples](https://orcheo.readthedocs.io/en/latest/examples/conversational_search/)** — Step-by-step demos from basic RAG to production-ready search

```bash
# Quick start: Run Demo 1 (no external services required)
uv sync --group examples
orcheo credential create openai_api_key --secret sk-your-key
python examples/conversational_search/demo_2_basic_rag/demo_2.py
```

Provider-aware chat models use provider-specific credential names in the active
vault by default. For example, `openai:gpt-4.1` looks for `openai_api_key`,
while `deepseek:deepseek-chat` looks for `deepseek_api_key`.

## Reference

- **[SDK Reference](https://orcheo.readthedocs.io/en/latest/sdk_reference/)** — Python SDK for programmatic workflow execution
- **[Environment Variables](https://orcheo.readthedocs.io/en/latest/environment_variables/)** — Complete configuration reference

## For Developers

- **[Developer Guide](https://orcheo.readthedocs.io/en/latest/manual_setup/#developer-guide)** — Repository layout, development environment, and custom nodes
- **[Deployment Guide](https://orcheo.readthedocs.io/en/latest/deployment/)** — Docker Compose and PostgreSQL deployment recipes
- **[Custom Nodes and Tools](https://orcheo.readthedocs.io/en/latest/custom_nodes_and_tools/)** — Extend Orcheo with your own integrations

## Contributing

We welcome contributions from the community:

- **Share your extensions**: Custom nodes, agent tools, and workflows that extend Orcheo's capabilities. See the [Custom Nodes and Tools guide](https://orcheo.readthedocs.io/en/latest/custom_nodes_and_tools/) for how to create and load custom extensions.
- **How to contribute**: Open an [issue](https://github.com/AI-Colleagues/orcheo/issues), submit a [pull request](https://github.com/AI-Colleagues/orcheo/pulls), or start a [discussion](https://github.com/AI-Colleagues/orcheo/discussions). You can also publish and share your extensions independently for others to install.

## Citation

If you use Orcheo in your research, please cite it as:

```bibtex
@article{jiang2026orcheo,
  author       = {Jiang, Shaojie and Vakulenko, Svitlana and de Rijke, Maarten},
  title        = {Orcheo: A Modular Full-Stack Platform for Conversational Search},
  journal      = {arXiv preprint arXiv:2602.14710},
  year         = {2026}
}
```
