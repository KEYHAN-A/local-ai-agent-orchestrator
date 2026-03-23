# Architecture

## Components

| Module | Role |
|--------|------|
| `cli.py` | Command routing, interactive flows (`lao`, `init`, `configure-models`), settings bootstrap |
| `interactive_ui.py` | Shared Rich primitives for headers, status tables, prompts, and guided menus |
| `console_ui.py` | Optional Rich full-screen run dashboard (`lao run` on a TTY) |
| `settings.py` | Runtime configuration (YAML + env + CLI) |
| `runner.py` | Main loop: plan ingestion, task dispatch, signals |
| `model_manager.py` | LM Studio REST `load` / `unload`, memory gate (`vm_stat`), JIT fallback |
| `phases.py` | `architect_phase`, `coder_phase`, `reviewer_phase` + OpenAI client |
| `state.py` | SQLite WAL: plans, micro_tasks, run_log |
| `tools.py` | Filesystem + shell + embedding search |
| `plan_git.py` | Optional per-plan Git: `git init`, plan snapshot, phase commits (`lao(plan|architect|coder|reviewer): …`) |
| `prompts.py` | System prompts and message builders |

## Execution flow

1. **Operator entry:** `lao` presents environment readiness and guides next action (`init`, `health`, `configure-models`, `run`).
2. **Plans:** New `.md` files under `plans/` (or `--plan`) are hashed; new content gets a `plan_id` and architect run.
2. **Architect:** Single chat completion; output parsed as JSON array of micro-tasks; inserted into SQLite.
3. **Coder:** For each pending task, load coder model, optional embedding retrieval, tool loop until final message. File tools run inside **`use_plan_workspace`**: **`<config_dir>/<plan-stem>/`** derived from the plan’s `.md` filename.
4. **Reviewer:** Same active workspace as the task’s plan. Load reviewer model, single completion; chain-of-thought blocks (e.g. Qwen3 / DeepSeek-R1 *think* tags) are stripped, then any line starting with `APPROVED` / `REJECTED` is used for the verdict; state transitions.
5. **Recovery:** On startup, tasks stuck in `coding` / `review` reset to safe states.

## Resume semantics

- Task state is persisted in SQLite (`micro_tasks`, `plans`, `run_log`) with WAL mode.
- On restart, `recover_interrupted()` moves transient states back to resumable states:
  - `coding` -> `pending`
  - `review` -> `coded`
- Plan deduplication is content-hash based: submitting unchanged plan text does not create a second decomposition path.

## Git commits (v1.3.0+)

When **`git.enabled`** is true, the orchestrator uses the **per-plan workspace** directory (`<config_dir>/<plan-stem>/`) as a Git repo:

1. **Before architect:** ensure `.git`, write **`LAO_PLAN.md`**, commit **`lao(plan): …`** (skipped if nothing staged).
2. **After architect:** write **`LAO_TASKS.json`**, commit **`lao(architect): …`**.
3. **After coder:** commit **`lao(coder): task #<id> …`** (files from tools are staged with `git add -A`).
4. **After reviewer:** append **`LAO_REVIEW.log`**, commit **`lao(reviewer): …`** (approved / rejected / failed).

Commits are skipped when there is nothing to stage (except reviewer, which always appends a log line when the hook runs). Existing user repos are respected (no re-init if `.git` exists).

## Model swapping

Only one large LLM should reside in VRAM at a time. `ModelManager.ensure_loaded` unloads other LLMs, runs the **memory gate** (wait until freed pages exceed a fraction of the unloaded model size), then calls `POST /api/v1/models/load`. LM Studio **guardrails** may block loads; the UI may allow “Load anyway” where the API does not—see README troubleshooting.

## Token discipline

Phases use **short system prompts** and **no Crew-style ReAct history** by default. Coder tool transcripts are truncated when long.
