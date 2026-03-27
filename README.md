# Local AI Agent Orchestrator (LAO)

<p align="center">
  <img alt="LAO interactive home menu with grouped actions" src="https://raw.githubusercontent.com/KEYHAN-A/local-ai-agent-orchestrator/main/docs/assets/lao-home-menu.png" width="780"/>
</p>

**LAO** (**v3.0.7+**) is a local coding factory for [LM Studio](https://lmstudio.ai/) and other **OpenAI-compatible** servers: a **planner → coder → reviewer** pipeline for long-running work, plus **Pilot Mode**—an interactive, agentic chat on your terminal that can run workspace tools, inspect the queue, create plans, switch projects, and hand control back to autopilot when you type **`/resume`**. Everything is backed by **SQLite**, **memory-aware model swapping**, optional **per-plan Git**, and a unified **TTY experience** (Rich + prompt_toolkit).

[![PyPI version](https://img.shields.io/pypi/v/local-ai-agent-orchestrator.svg?label=PyPI&logo=pypi)](https://pypi.org/project/local-ai-agent-orchestrator/)
[![Python versions](https://img.shields.io/pypi/pyversions/local-ai-agent-orchestrator.svg)](https://pypi.org/project/local-ai-agent-orchestrator/)
[![GitHub release](https://img.shields.io/github/v/release/KEYHAN-A/local-ai-agent-orchestrator?logo=github&label=release)](https://github.com/KEYHAN-A/local-ai-agent-orchestrator/releases/latest)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-KEYHAN--A%2Flocal--ai--agent--orchestrator-181717?logo=github)](https://github.com/KEYHAN-A/local-ai-agent-orchestrator)
[![Website](https://img.shields.io/badge/Website-lao.keyhan.info-222?logo=googlechrome)](https://lao.keyhan.info)

| Resource | Link |
|----------|------|
| **PyPI** | [pypi.org/project/local-ai-agent-orchestrator](https://pypi.org/project/local-ai-agent-orchestrator/) |
| **Website** | [lao.keyhan.info](https://lao.keyhan.info) |
| **Repository** | [github.com/KEYHAN-A/local-ai-agent-orchestrator](https://github.com/KEYHAN-A/local-ai-agent-orchestrator) |
| **Issues** | [GitHub Issues](https://github.com/KEYHAN-A/local-ai-agent-orchestrator/issues) |
| **License** | [GPL-3.0-only](LICENSE) |
| **Changelog** | [CHANGELOG.md](CHANGELOG.md) |

---

## Table of contents

- [Installation](#installation)
- [Features](#features)
- [LAO Pilot Mode](#lao-pilot-mode-v304)
- [Project commands and multi-repo workflows](#project-commands-and-multi-repo-workflows)
- [Why the OpenAI SDK (not LangChain / CrewAI)?](#why-the-openai-sdk-not-langchain--crewai)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration overview](#configuration-overview)
- [CLI reference](#cli-reference)
- [Workspaces, paths, and resume behavior](#workspaces-paths-and-resume-behavior)
- [Git traceability](#git-traceability)
- [Architecture](#architecture)
- [Documentation](#documentation)
- [Security](#security)
- [Contributing](#contributing)
- [Releases](#releases)

---

## Installation

### From PyPI (recommended)

```bash
pip install local-ai-agent-orchestrator
pip install -U local-ai-agent-orchestrator   # upgrade
```

The CLI entry point is **`lao`** (also `local-ai-agent-orchestrator`).

### One-line installer (curl)

The canonical script in git is [`scripts/install.sh`](scripts/install.sh). It prefers **pipx** when available (isolated app environment), otherwise **`pip install --user`**.

**Short URL (GitHub Pages):**

```bash
curl -fsSL https://lao.keyhan.info/install.sh | bash
```

That bootstrap is a tiny script that downloads the same implementation from GitHub `main`—so there is only one full installer to maintain.

**Direct from GitHub (easy to audit against the repo):**

```bash
curl -fsSL https://raw.githubusercontent.com/KEYHAN-A/local-ai-agent-orchestrator/main/scripts/install.sh | bash
```

Trust trade-off: piping to `bash` always means you trust the host and transport. Many people prefer the **raw.githubusercontent.com** URL because the path maps cleanly to `main/scripts/install.sh` in this repository. The **lao.keyhan.info** URL is the same behavior after one redirect through the small bootstrap.

Optional environment variables: **`LAO_VERSION`** (pin a release, e.g. `3.0.7`), **`LAO_PACKAGE`** (override PyPI name).

### Homebrew ecosystem

LAO is a **Python** package on PyPI. Homebrew does not replace PyPI here; the usual approach on macOS is to use Homebrew’s **pipx** and install LAO into an isolated environment:

```bash
brew install pipx
pipx ensurepath
pipx install local-ai-agent-orchestrator
# later: pipx upgrade local-ai-agent-orchestrator
```

A first-party **Homebrew formula in homebrew-core** is possible but requires vendoring every Python dependency with fixed URLs and checksums (no PyPI access during `brew install`). If you want a custom **tap** (`brew install yourname/tap/lao`), generate or hand-maintain a formula with something like [homebrew-pypi-poet](https://github.com/tdsmith/homebrew-pypi-poet) or follow [Homebrew’s Python guide](https://docs.brew.sh/Python-for-Formula-Authors).

### npm

LAO is implemented in **Python**, not JavaScript. Publishing an **npm** package that only shells out to `pip` would be confusing to npm users and fragile across machines. Use **pip**, **pipx**, or the curl installer above instead.

### Editable install (development)

```bash
git clone https://github.com/KEYHAN-A/local-ai-agent-orchestrator.git
cd local-ai-agent-orchestrator
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

### Without installing the package

```bash
python main.py health
```

(`main.py` adds `src/` for you.)

---

## Features

### End-to-end pipeline

- **Planner (architect):** Decomposes a master plan into a JSON array of micro-tasks (title, description, file paths, dependencies). Large plans are **chunked** to fit the planner context; completed chunks are **resumed** instead of recomputed.
- **Task queue:** **SQLite** (WAL) stores plans, tasks, run logs, and structured review findings. **Dependency-aware** scheduling: tasks wait on prerequisites; dependents of **failed** tasks are failed with explicit feedback.
- **Coder:** OpenAI-style chat with **tool calling** (when the model supports it): `file_read`, `file_write`, `file_patch`, `list_dir`, `shell_exec`. Work runs inside the **active plan workspace** (see below).
- **Reviewer:** Single completion that must yield structured JSON (`verdict`, `findings`, `summary`). Feeds back into queue state (approve, rework, or fail after max attempts).

### State, recovery, and operator tools

- **Resume by default:** On startup, tasks stuck in transient phases are reset (`coding` → `pending`, `review` → `coded`) via `recover_interrupted()`.
- **Plan deduplication:** Identical plan **content** is hashed; resubmitting the same text does not spawn a second decomposition.
- **`lao retry-failed`:** Moves `failed` tasks back to `pending` for another pass. **`lao reset-failed`** is a deprecated alias.

### Models and memory

- **Role-specific models** in `factory.yaml` (`planner`, `coder`, `reviewer`, **`pilot`**, optional `embedder`).
- **ModelManager** loads/unloads via LM Studio’s HTTP API so only one large LLM tends to sit in VRAM at a time.
- **Memory gate:** After unload, waits until freed memory (via `vm_stat` on macOS) meets configured thresholds before loading the next model.
- **LLM retries:** Configurable timeouts, attempts, and exponential backoff for transient API errors.

### Semantic context (embedder)

- When configured, **embedding search** (`find_relevant_files`) can rank files before coding so the coder prompt includes short excerpts from likely-relevant paths (see `tools.py`).

### Quality gates and validation

- **Post-coder validation** (placeholder text, selected code smells, optional **`validation_build_cmd`** / **`validation_lint_cmd`** when set in config) produces **findings**; severity drives gating when `quality_gate_mode` is `standard` or `strict`.
- **Per-plan `quality_report.json`** summarizes runs and findings for traceability (see `reporting` module and docs).

### Reviewer robustness (local models)

- **Chain-of-thought stripping:** `<think>…</think>`-style blocks are removed before parsing reviewer output (R1 / Qwen-style models).
- **JSON verdict parsing:** Accepts raw JSON, JSON inside **markdown code fences**, or a JSON object embedded in surrounding prose—so `APPROVED` inside a fenced block is not misread as a rejection.

### Optional Git traceability

- When enabled, each plan’s project directory can be a Git repo: **`LAO_PLAN.md`**, **`LAO_TASKS.json`**, phase commits with subjects like **`lao(coder): task #42 …`**, and **`LAO_REVIEW.log`** appended after review. Disable globally in YAML or per run with **`--no-git`**.

### Operator experience

- **`lao` (no subcommand):** Interactive home on a TTY—environment status, grouped menu (initialize workspace + pilot first, then other actions; **Exit** last).
- **`lao init`:** Scaffold `factory.yaml` / `factory.example.yaml`, `.lao/`, `plans/`, optional workspace `README.md`.
- **`lao configure-models`:** Interactive remap of model keys to match `lms ls` / LM Studio.
- **`lao run`:** Orchestrator loop with unified TTY UI when idle transitions into **Pilot Mode** (configurable). **`--plain`** yields classic timestamped logs (CI, pipes, debugging).

---

## LAO Pilot Mode (v3.0.4+)

**Pilot Mode** is the interactive layer when the pipeline is idle—or when you run **`lao pilot`** / choose **Pilot** from the home menu. The Pilot uses the same **OpenAI tools** pattern as the coder (read/write/patch files, shell, semantic search) and adds pipeline controls (`pipeline_status`, `create_plan`, `retry_failed`, `resume_pipeline`, `project_status`, …).

- **Unified UI:** Scrollback for chat and activity, compact status line (phase / model / task), slash hints (`/help`, `/status`, `/resume`, `/clear`, `/exit`, `/project …`).
- **Project awareness:** **`lao projects`** (list, scan, add, use, needs-action, remove) and in-chat **`/project`** to list, scan, switch workspace, or show status. Registry stored at **`~/.lao/projects.json`**. Intent phrases and paths can trigger a workspace switch before the tool loop.
- **Guardrails:** Repeated tool errors back off and ask for a clearer path; absolute paths like `/Users/...` are **not** mis-parsed as slash commands.
- **Exiting chat:** Double **Ctrl+C** exits the prompt; **Ctrl+D** (EOF) also ends input.

### Screenshots
<p align="center">
  <b>Pilot Mode</b><br/>
  <img alt="LAO Pilot Mode chat with status bar and LAO ASCII branding" src="https://raw.githubusercontent.com/KEYHAN-A/local-ai-agent-orchestrator/main/docs/assets/lao-pilot-mode.png" width="780"/>
</p>

*(Images are also in-repo under [`docs/assets/`](docs/assets/) for the site and offline viewing.)*

### Operator flow (autopilot + pilot)

```mermaid
flowchart LR
  home["lao home"] --> init["lao init"]
  home --> pilotEntry["lao pilot"]
  home --> run["lao run"]
  run --> work["Planner Coder Reviewer"]
  work --> idle{Queue idle?}
  idle -->|yes| pilot["Pilot chat"]
  idle -->|no| work
  pilot --> resume["/resume or tool"]
  resume --> work
  pilot --> home2["Exit"]
```

Full release notes: **[CHANGELOG.md](CHANGELOG.md)** (latest: `v3.0.7`; Pilot highlights in `v3.0.4`).

---

## Project commands and multi-repo workflows

If you start LAO from a **parent directory** (no `factory.yaml` in the current folder), use **`lao projects scan`** (or the home menu **Scan for LAO projects**) to discover repos that have `factory.yaml`, `plans/*.md`, or `.lao/state.db`. **`lao projects use <name-or-path>`** re-binds configuration to that project. In Pilot chat, **`/project use …`** performs the same switch for the session.

---

## Why the OpenAI SDK (not LangChain / CrewAI)?

LAO calls the **OpenAI Python SDK** directly against your local server to avoid heavy multi-agent framework scaffolding and extra token overhead on **small local context windows**.

---

## Requirements

- **Python 3.10+**
- **LM Studio** (or compatible server) with the API enabled
- Model **keys** in `factory.yaml` that match what the server exposes (use `lao health` or `lms ls`)
- **`git`** on `PATH` if you use Git traceability, with `user.name` / `user.email` configured
- **Apple Silicon:** if large models fail to load, relax LM Studio **Model Loading Guardrails** (Developer → Server Settings)

Full reference: **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

---

## Quick start

```bash
lao                  # interactive home (TTY) — init, pilot, run, projects, …
lao init             # scaffold config, .lao/, plans/

lao health           # LM Studio reachability + configured model keys
lao run              # watch plans/, run pipeline; enters Pilot when idle (TTY)
lao pilot            # jump straight into Pilot chat (after health check)

# Parent folder without factory.yaml: discover and register projects
lao projects scan
lao projects use my-repo

# Alternative: one plan, one pass
lao --plan plans/my_project.md --single-run run
```

Drop new **`*.md`** files into your configured **`plans/`** directory (by default next to `factory.yaml`). **`plans/README.md`** is never ingested as a plan.

---

## Configuration overview

Configuration lives in **`factory.yaml`** (or path from **`LAO_CONFIG`** / **`--config`**). Typical areas:

| Area | Purpose |
|------|---------|
| **`lm_studio_base_url`**, **`openai_api_key`** | Server endpoint and API key (LM Studio often uses a placeholder key). |
| **`paths.plans`**, **`paths.database`** | Where plans are scanned and where **SQLite** lives (default **`.lao/state.db`**). |
| **`memory_gate.*`** | Release fraction, swap growth limits, settle timeout, poll interval. |
| **`orchestration.*`** | Load timeouts, **`max_task_attempts`**, watch interval, LLM timeouts/retries, **`phase_gated`**, **`coder_batch_size`**, **`reviewer_batch_size`**, **`max_context_utilization`**, **`quality_gate_mode`**, **`pilot_mode_enabled`**, optional **`validation_build_cmd`** / **`validation_lint_cmd`**. |
| **`git.*`** | Enable/disable traceability, plan snapshot filename, optional commit trailers. |
| **`models.*`** | Per-role **`key`**, **`context_length`**, **`max_completion`**, **`supports_tools`**, size hints for memory accounting. Roles include **`pilot`** for Pilot Mode. |

Environment variables (including **`LM_STUDIO_BASE_URL`**, **`OPENAI_API_KEY`**, **`TOTAL_RAM_GB`**, **`WORKSPACE_ROOT`**, **`PLANS_DIR`**, **`DB_PATH`**) are documented in **[.env.example](.env.example)**.

---

## CLI reference

### Commands

| Command | Description |
|---------|-------------|
| `lao` | Interactive home: environment status and grouped actions (TTY). |
| `lao run` | Watch `plans/`, run architect/coder/reviewer loop until interrupted; on TTY, **Pilot Mode** when idle unless `--no-pilot`. |
| `lao pilot` | Enter **Pilot Mode** immediately (LM Studio must be reachable). |
| `lao projects` | Manage known workspaces: `list` (default), `scan`, `add`, `use`, `remove`, `needs-action`. Optional `--root`, `--tag` on `add`. |
| `lao init` | Onboarding scaffold: `factory.example.yaml`, `.lao/`, `plans/`, optional `README.md`. Flags: `--skip-readme`, `--no-interactive`. |
| `lao health` | Check server reachability and that configured model keys exist. |
| `lao status` | SQLite queue summary and token totals. |
| `lao configure-models` | Interactive update of role model keys (`planner`, `coder`, `reviewer`, `embedder`, `pilot`) in `factory.yaml`. |
| `lao preflight` | Plan context diagnostics: `--plan PATH` (required). |
| `lao benchmark` | Core reliability benchmark suite; writes report under config dir. |
| `lao kpi` | KPI snapshot for tracking. |
| `lao dashboard` | Operator dashboard snapshot. |
| `lao report` | Quality report schema: `check` or `migrate` (`--file` optional). |
| `lao retry-failed` | Reset **failed** tasks to **pending** for another attempt. |
| `lao reset-failed` | Deprecated alias for **`retry-failed`**. |

`lao run` accepts **`--plan PATH`** (single plan) and **`--single-run`** (one scheduler pass then exit).

### Global flags

| Flag | Description |
|------|-------------|
| `--config PATH` | Path to `factory.yaml` (default: `./factory.yaml` if present). |
| `--lm-studio-url URL` | Override LM Studio base URL. |
| `--ram-gb N` | Total RAM in GB (logged; reserved for future tuning). |
| `--workspace`, `--plans-dir`, `--db` | Override workspace, plans directory, and SQLite path. |
| `--planner-model`, `--coder-model`, `--reviewer-model`, `--embedder-model`, `--pilot-model` | Override model keys without editing YAML. |
| `--plain` | Classic scrolling log instead of the unified TTY experience. |
| `--no-git` | Disable Git snapshots/commits for this run (overrides `factory.yaml`). |
| `--no-pilot` | Disable Pilot Mode during **`lao run`** (legacy idle behavior). |
| `--pilot-only` | Jump straight into **Pilot Mode** (works with **`lao run`** as well as `lao pilot`). |
| `--phase-gated` | Enable role-batched phase execution (coder/reviewer waves) for this run. |
| `--batch-size N` | Coder batch size override for this run. |
| `--max-context-utilization RATIO` | Planner context utilization hint (0–1). |
| `--quality-gate` | Override quality gate: `strict`, `standard`, or `off`. |

---

## Workspaces, paths, and resume behavior

- **Per-plan project directory:** For a plan file `plans/MyPlan.md`, the default workspace is **`<config_dir>/MyPlan/`** (same stem as the plan), i.e. next to your `plans/` folder after `lao init`. The coder’s file tools operate **inside that directory** (with safety checks).
- **Fallback:** If a plan has no normal stem, **`.lao/_misc/`** can be used as a fallback workspace (see configuration docs).
- **State database:** Default **`.lao/state.db`** unless overridden.
- **Resume:** Restarting **`lao run`** continues from SQLite; interrupted phases are recovered automatically.

On a TTY, **`lao run`** uses the **unified LAO shell** (status + activity + pilot when idle). Use **`--plain`** for logs suitable for CI or redirection.

---

## Git traceability

When **`git.enabled`** is true (default), LAO uses **`<config_dir>/<plan-stem>/`** as the Git working tree:

1. **Plan snapshot:** **`LAO_PLAN.md`** committed when appropriate (`lao(plan): …`).
2. **After architect:** **`LAO_TASKS.json`** (`lao(architect): …`).
3. **After coder:** staged changes (`lao(coder): task #…`).
4. **After reviewer:** **`LAO_REVIEW.log`** updated (`lao(reviewer): …`).

Disable with **`git.enabled: false`** or **`lao --no-git run`**. Existing **`.git`** directories are respected (no forced re-init).

---

## Architecture

Module-level detail: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

### Pipeline overview

```mermaid
flowchart LR
  plansMd["plans/*.md"] --> register[Register_and_hash]
  register --> plannerLLM[Planner_LLM]
  plannerLLM --> sqliteQ[("SQLite_queue")]
  sqliteQ --> coderLLM[Coder_LLM]
  coderLLM --> reviewerLLM[Reviewer_LLM]
  reviewerLLM --> sqliteQ
  coderLLM -.->|optional| gitTrace[Git_traceability]
  reviewerLLM -.->|optional| gitTrace
```

### Model loading and memory gate

```mermaid
flowchart TB
  subgraph roles [Phases_need_models]
    direction LR
    rolePlanner[planner] --> roleCoder[coder] --> roleReviewer[reviewer]
  end
  rolePlanner --> mm[ModelManager_ensure_loaded]
  roleCoder --> mm
  roleReviewer --> mm
  mm --> unload[Unload_other_LLMs]
  unload --> gate[Memory_gate]
  gate --> load[LM_Studio_load_API]
```

---

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, execution flow, resume, Git, model swapping |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | `factory.yaml`, paths, orchestration, Git |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | How to contribute |
| [docs/PYPI_PUBLISH.md](docs/PYPI_PUBLISH.md) | Maintainer: publishing to PyPI |
| [lao.keyhan.info](https://lao.keyhan.info) | Project site (from [docs/index.html](docs/index.html)) |

---

## Security

LAO can **execute shell commands** and **write files** in the configured workspace as driven by the coder and your plan. Run only in **trusted** directories, use **`--no-git`** or disable tools if you need a read-only mental model, and review **`factory.yaml`** before production use. This project is **GPL-3.0-only**; dependencies have their own licenses.

---

## Contributing

Issues and pull requests are welcome. See **[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)** for guidelines.

---

## Releases

- **Latest changes:** see **[CHANGELOG.md](CHANGELOG.md)** (full history from v1.0.0).
- **Install the latest build:** `pip install -U local-ai-agent-orchestrator`
- **GitHub Releases:** [github.com/KEYHAN-A/local-ai-agent-orchestrator/releases](https://github.com/KEYHAN-A/local-ai-agent-orchestrator/releases)

**Recent highlights (v3.0.7):** README opens with the **home menu** screenshot; **Installation** stays up front in the TOC; **LAO Pilot Mode** (v3.0.4+) adds interactive agentic chat, project registry (`lao projects`, `/project`), grouped home menu, and hardened terminal UX. See **[CHANGELOG.md](CHANGELOG.md)**.
