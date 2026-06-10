# Requirements Document

## METADATA
- **Authors:** Claude
- **Project/Feature Name:** Deep Agent Skill Management — Skills-Aware Deep Agents for Orcheo
- **Type:** Feature
- **Summary:** A unified initiative covering (1) an `orcheo skill` CLI for managing Agent Skills — the open, portable format for extending AI agents — stored in `~/.orcheo/skills/`, and (2) a `DeepAgentNode` that wraps LangChain's `create_deep_agent` for autonomous multi-step research, with automatic discovery and loading of installed skills by default.
- **Owner (if different than authors):** Shaojie Jiang
- **Date Started:** 2026-03-21

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Agent Skills Specification | https://agentskills.io/specification | Community | Open standard |
| Agent Skills Overview | https://agentskills.io/what-are-skills | Community | What are skills |
| Plugin CLI Reference | `packages/sdk/src/orcheo_sdk/cli/plugin.py` | ShaojieJiang | Plugin CLI pattern |
| Plugin Manager | `src/orcheo/plugins/manager.py` | ShaojieJiang | Plugin manager pattern |
| Node Architecture | `src/orcheo/nodes/base.py` | ShaojieJiang | Node base classes |
| Existing AgentNode | `src/orcheo/nodes/ai.py` | ShaojieJiang | AI agent node implementation |
| Node Registry | `src/orcheo/nodes/registry.py` | ShaojieJiang | Node registry system |
| LangChain DeepAgents Docs | https://docs.langchain.com/oss/python/deepagents/overview | LangChain | DeepAgents overview |
| Design | `project/initiatives/deep_agent_skill_management/2_design.md` | ShaojieJiang | Design |
| Plan | `project/initiatives/deep_agent_skill_management/3_plan.md` | ShaojieJiang | Plan |

## PROBLEM DEFINITION

### Objectives
1. Provide a CLI-driven system for installing, inspecting, and managing Agent Skills on the Orcheo server, enabling agents within Orcheo workflows to discover and load on-demand procedural knowledge, scripts, and resources from the open Agent Skills format.
2. Provide a first-class `DeepAgentNode` that wraps `create_deep_agent` for autonomous multi-step research within workflows.
3. Connect skills to deep agents: installed skills should be automatically discovered and loaded by `DeepAgentNode` by default, so that workflow authors get skills-augmented research agents without manual configuration.

### Target users
- Workflow authors who want to extend agent capabilities with domain-specific knowledge (legal review, data analysis, code generation patterns).
- Workflow authors who need autonomous research capabilities (e.g., competitive analysis, literature reviews, data gathering) within their Orcheo workflows.
- Teams that maintain organizational knowledge as portable, version-controlled skill packages.
- Platform operators who want to curate a set of approved skills for their Orcheo deployment.

### User Stories

| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Workflow author | Run `orcheo skill install <path-or-url>` to add a new skill | My agents can discover and use the skill's instructions and tools | P0 | Skill is copied to `~/.orcheo/skills/<skill-name>/`, validated, and shows in `orcheo skill list` |
| Workflow author | Run `orcheo skill list` to see all installed skills | I know which skills are available for my agents | P0 | Lists name, description, and status of all installed skills |
| Workflow author | Run `orcheo skill show <name>` to inspect a skill | I can review the skill's metadata, description, and file structure | P0 | Displays full SKILL.md frontmatter, compatibility info, and file listing |
| Workflow author | Run `orcheo skill uninstall <name>` to remove a skill | I can clean up skills I no longer need | P0 | Skill directory removed from `~/.orcheo/skills/`, no longer in `orcheo skill list` |
| Workflow author | Add a DeepAgentNode to my workflow that autonomously researches a topic using multiple tools | I can build research-intensive workflows without manually orchestrating multi-step tool calls | P0 | DeepAgentNode accepts a research query, uses configured tools, and returns a synthesized result |
| Workflow author | Configure the DeepAgentNode with custom tools, model, and max iterations | I can control the agent's behaviour, cost, and scope | P0 | Node exposes `ai_model`, `predefined_tools`, `mcp_servers`, `max_iterations` fields |
| Workflow author | Have DeepAgentNode automatically use all installed skills without extra configuration | Agents are skills-aware out of the box after I install skills via `orcheo skill` | P0 | When `skills` field is not set, DeepAgentNode discovers and loads all skills from `~/.orcheo/skills/` |
| Workflow author | Override the default skill loading by setting `skills` explicitly | I can limit which skills a specific agent uses, or point to custom skill directories | P1 | Setting `skills` to an explicit list bypasses auto-discovery |
| Developer | Run `orcheo skill validate <path>` to check a skill before installing | I can verify my skill follows the Agent Skills specification | P1 | Reports validation errors (missing name, bad name format, missing description) |
| Team lead | Install skills from a git repository | I can version-control and share skills across my team | P1 | `orcheo skill install git+https://...` clones and installs the skill |

### Context, Problems, Opportunities

Agent Skills are an open, portable format (originated by Anthropic, adopted by 30+ agent products) for giving AI agents specialized capabilities. A skill is a folder containing a `SKILL.md` file with YAML frontmatter (name, description) and Markdown instructions, plus optional scripts, references, and assets.

Skills are broader than tools — they encapsulate procedural knowledge, domain expertise, and multi-step workflows that agents can discover and load on demand. They differ from Orcheo plugins in that:
- **Plugins** extend the Orcheo platform itself (register new nodes, edges, triggers, listeners).
- **Skills** extend the *agents within workflows* with domain knowledge and instructions.

Orcheo's existing `AgentNode` wraps LangChain's `create_agent` for single-pass agent execution. However, many real-world use cases — competitive analysis, multi-source data gathering, research synthesis — require agents that autonomously plan multi-step research, iterate over tool results, and synthesize findings.

LangChain's `deepagents` package provides `create_deep_agent`, which builds on `create_agent` with additional middleware: a todo-list planner, virtual file-system backend, sub-agent spawning, context summarisation, and **skills loading**. By wrapping this as a `DeepAgentNode` and connecting it to the Orcheo skill store, we get a complete pipeline: install skills via CLI, then deep agents automatically discover and use them.

### Product Goals and Non-goals

**Goals:**
- Implement `orcheo skill` CLI command group (install, list, show, uninstall, validate) following the pattern of `orcheo plugin`.
- Store installed skills in `~/.orcheo/skills/` with a state file tracking installations.
- Validate skills against the Agent Skills specification (SKILL.md frontmatter schema).
- Provide a `DeepAgentNode` that extends `AINode` with deep-research capabilities.
- Automatically discover and load all installed skills into `DeepAgentNode` by default (when `skills` field is not explicitly set).
- Support all tool sources already available to `AgentNode`: predefined tools, workflow tools, and MCP servers.

**Non-goals:**
- A skill marketplace or registry service.
- Skill authoring wizard or scaffolding (future enhancement).
- Real-time streaming of intermediate research steps to Studio (future enhancement).
- Building a custom agent framework outside of LangChain/deepagents.

## PRODUCT DEFINITION

### Requirements

**P0: Skill storage and state management**
- Skills are stored as directories under `~/.orcheo/skills/<skill-name>/`.
- State tracked in `~/.orcheo/skills/skills.toml` (installed skill records with name, source, install date).
- Environment variable `ORCHEO_SKILLS_DIR` overrides the default storage path.

**P0: SKILL.md parsing and validation**
- Parse YAML frontmatter from `SKILL.md` files.
- Validate required fields: `name` (1-64 chars, lowercase alphanumeric + hyphens, no leading/trailing/consecutive hyphens), `description` (1-1024 chars, non-empty).
- Validate optional fields when present: `license`, `compatibility` (max 500 chars), `metadata` (string-to-string map), `allowed-tools`.
- Validate that `name` matches the parent directory name.

**P0: CLI commands**
- `orcheo skill list` — list all installed skills with name, description, and status.
- `orcheo skill show <name>` — show full skill metadata, description, and file listing.
- `orcheo skill install <ref>` — install a skill from a local path. Copies the skill directory to `~/.orcheo/skills/<name>/`. Validates SKILL.md before installing.
- `orcheo skill uninstall <name>` — remove skill directory and state record.

**P0: DeepAgentNode implementation**
- New node class `DeepAgentNode` inheriting from `AINode`.
- Configurable fields: `ai_model`, `system_prompt`, `research_prompt`, `predefined_tools`, `workflow_tools`, `mcp_servers`, `max_iterations` (default 100), `model_kwargs`, `response_format`, `skills`, `memory`, `debug`.
- Uses `create_deep_agent` from the `deepagents` package with `recursion_limit` set to `max_iterations`.
- Registered in node registry under category `"ai"` with name `"DeepAgentNode"`.

**P0: Default skill loading in DeepAgentNode**
- When `DeepAgentNode.skills` is `None` (the default), automatically discover all installed skill directories from `~/.orcheo/skills/` and pass their paths to `create_deep_agent`.
- When `skills` is explicitly set to a list, use only those paths (no auto-discovery).
- Respects `ORCHEO_SKILLS_DIR` environment variable for skill directory resolution.

**P1: Extended installation sources**
- `orcheo skill install <git-url>` — clone a git repository and install the skill.
- `orcheo skill validate <path>` — validate a skill directory without installing.

**P0: Testing**
- 100% test coverage for all new modules.

### Other Teams Impacted
- **Studio Frontend:** Node catalog will show the new DeepAgentNode (automatic via registry).
- **Documentation:** CLI reference docs and node catalog docs need updating.

## TECHNICAL CONSIDERATIONS

### Architecture Overview

```
~/.orcheo/skills/
  ├── skills.toml          # State file tracking installations
  ├── pdf-processing/
  │   ├── SKILL.md         # Required: metadata + instructions
  │   ├── scripts/         # Optional: executable code
  │   └── references/      # Optional: documentation
  └── code-review/
      ├── SKILL.md
      └── assets/

orcheo skill CLI (packages/sdk)
  ├── orcheo skill list       → reads skills.toml + skill directories
  ├── orcheo skill show       → reads SKILL.md frontmatter + file listing
  ├── orcheo skill install    → validates SKILL.md, copies to skills dir
  ├── orcheo skill uninstall  → removes directory + state record
  └── orcheo skill validate   → validates SKILL.md without installing

Core skill library (src/orcheo/skills/)
  ├── models.py     # Data models (SkillMetadata, SkillRecord, etc.)
  ├── paths.py      # Storage path helpers
  ├── manager.py    # Skill lifecycle operations
  └── parser.py     # SKILL.md frontmatter parsing and validation

DeepAgentNode (src/orcheo/nodes/deep_agent.py)
  ├── Inherits AINode
  ├── Tool preparation: reuses _prepare_tools() from AgentNode
  ├── Skill resolution: if skills=None → discover from ~/.orcheo/skills/
  ├── Agent creation: create_deep_agent with recursion_limit=max_iterations
  └── Built-in middleware: planning, file-system, sub-agents, summarisation
```

### Technical Requirements
- Python 3.12+, `deepagents` package (`create_deep_agent` API).
- YAML frontmatter parsed via a lightweight built-in approach (split on `---` delimiters, then simple key-value extraction).
- Skills are filesystem-based — no database required.
- Must pass `make lint` (ruff, mypy strict) and `make test` with 100% coverage.

## LAUNCH/ROLLOUT PLAN

### Success metrics

| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Skill installation | >= 10 skills installed across all deployments within 30 days |
| [Primary] DeepAgentNode adoption | >= 5 workflows using DeepAgentNode within 30 days of release |
| [Secondary] Custom skill authoring | >= 3 teams create custom skills within 60 days |
| [Guardrail] CLI responsiveness | `orcheo skill list` < 500ms for 50 installed skills |
| [Guardrail] Execution time | p95 execution < 5 minutes for 10-iteration research tasks |

### Rollout Strategy
Ship skill CLI in the Orcheo SDK release and DeepAgentNode in the core Orcheo release. Skills are a local-first feature. DeepAgentNode is opt-in (users explicitly add it to workflows). Skills become immediately effective once installed — no workflow reconfiguration needed.

## HYPOTHESIS & RISKS

**Hypothesis:** Workflow authors who install domain-specific skills will see improved agent research quality through DeepAgentNode's automatic skill loading, reducing per-workflow prompt engineering effort and improving agent reliability.

**Risks:**
- Users may install large skill directories that consume disk space. Mitigation: `orcheo skill list` shows directory size; future enhancement could add size limits.
- Skill quality varies across community contributions. Mitigation: `orcheo skill validate` helps users vet skills before installation.
- Skill name collisions when installing from different sources. Mitigation: skill names must match directory names and are unique within the installation directory.
- Deep research agents may consume significant tokens and time. Mitigation: `max_iterations` field with a sensible default (100) gives users explicit control.
- Loading many installed skills may increase context size. Mitigation: users can set `skills` explicitly to limit which skills are loaded for a given agent.

## APPENDIX
