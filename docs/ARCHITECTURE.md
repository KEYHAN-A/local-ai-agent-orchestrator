# Architecture

## Components

| Module | Role |
|--------|------|
| `cli.py` | Command routing, interactive home (`lao`), `init`, `configure-models`, **`lao pilot`**, **`lao projects`**, settings bootstrap |
| `interactive_ui.py` | Shared Rich primitives for headers, status tables, prompts, guided menus, **questionary** selects (incl. menu separators) |
| `unified_ui.py` | **Unified TTY shell** for pilot + runner: `TerminalCapabilities`, `RenderBus`, `ViewComposer`, `TerminalShell` (Rich + **prompt_toolkit**), `LogBridge` |
| `pilot.py` | **PilotAgent** chat loop, tool dispatch, slash commands, project intent/resolver, consecutive tool-error guard |
| `pilot_tools.py` | Pilot-only tools (`create_plan`, `pipeline_status`, `retry_failed`, `resume_pipeline`, `project_status`, …) + workspace tool schemas |
| `project_registry.py` | **ProjectRegistry** / `ProjectEntry`: scan/add/list/`~/.lao/projects.json` persistence |
| `console_ui.py` | Legacy/plain helpers; run dashboard superseded by unified UI on TTY |
| `settings.py` | Runtime configuration (YAML + env + CLI) |
| `runner.py` | Main loop: plan ingestion, task dispatch, signals; wires **UnifiedUI** + **PilotAgent** when idle |
| `model_manager.py` | LM Studio REST `load` / `unload`, memory gate (`vm_stat`), JIT fallback |
| `phases.py` | `architect_phase`, `coder_phase`, `reviewer_phase` + OpenAI client |
| `state.py` | SQLite WAL: plans, micro_tasks, run_log |
| `tools.py` | Filesystem + shell + embedding search |
| `plan_git.py` | Optional per-plan Git: `git init`, plan snapshot, phase commits (`lao(plan|architect|coder|reviewer): …`) |
| `prompts.py` | System prompts and message builders |

## Execution flow

1. **Operator entry:** `lao` presents environment readiness and grouped next actions (`init`, **Pilot**, `run`, **`projects`**, scan, …). **`lao pilot`** or **`lao projects`** bypass the home menu.
2. **Idle / Pilot:** When `orchestration.pilot_mode_enabled` is true (default), **`lao run`** on a TTY hands off to **PilotAgent** after pipeline work drains; **`TaskQueue`** is bound for tools; **`/resume`** or **`resume_pipeline`** returns to the autopilot loop. **UnifiedUI** renders scrollback, status bar, and prompt (**double Ctrl+C** exits the prompt).
3. **Plans:** New `.md` files under `plans/` (or `--plan`) are hashed; new content gets a `plan_id` and architect run.
4. **Architect:** The plan text may be **split into chunks** to fit the planner context. Each chunk is sent to the planner model; output is parsed as a **JSON array** of micro-tasks. Chunk results are merged, deduplicated at the plan level, and persisted to SQLite. Completed chunks can be **skipped on resume** if already stored.
5. **Scheduler:** With **`phase_gated`**, the runner may batch **coder** work then **reviewer** work in waves (`coder_batch_size`, `reviewer_batch_size`). Otherwise it alternates coder → reviewer per task. Dependencies constrain which **pending** tasks are eligible.
6. **Coder:** For each pending task, load coder model, optional embedding retrieval (`find_relevant_files`), tool loop until final message. File tools run inside **`use_plan_workspace`**: **`<config_dir>/<plan-stem>/`** derived from the plan’s `.md` filename.
7. **Reviewer:** Same active workspace as the task’s plan. Load reviewer model, single completion; chain-of-thought blocks (e.g. Qwen3 / DeepSeek-R1 *think* tags) are stripped, then output is parsed as **structured JSON** (`verdict`, `findings`, `summary`)—including JSON inside **markdown fences** or embedded in prose. Optional static **validators** may add findings and reject before the LLM review when quality gates are strict enough. State transitions: completed, rework (with feedback), or failed after max attempts.
8. **Recovery:** On startup, tasks stuck in `coding` / `review` reset to safe states.

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
