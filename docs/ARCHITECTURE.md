# Architecture

## Components

| Module | Role |
|--------|------|
| `cli.py` | Argument parsing, `factory.yaml` loading, `init_settings()` |
| `settings.py` | Runtime configuration (YAML + env + CLI) |
| `runner.py` | Main loop: plan ingestion, task dispatch, signals |
| `model_manager.py` | LM Studio REST `load` / `unload`, memory gate (`vm_stat`), JIT fallback |
| `phases.py` | `architect_phase`, `coder_phase`, `reviewer_phase` + OpenAI client |
| `state.py` | SQLite WAL: plans, micro_tasks, run_log |
| `tools.py` | Filesystem + shell + embedding search |
| `prompts.py` | System prompts and message builders |

## Execution flow

1. **Plans:** New `.md` files under `plans/` (or `--plan`) are hashed; new content gets a `plan_id` and architect run.
2. **Architect:** Single chat completion; output parsed as JSON array of micro-tasks; inserted into SQLite.
3. **Coder:** For each pending task, load coder model, optional embedding retrieval, tool loop until final message. File tools run inside **`use_plan_workspace`**: `.lao/workspaces/<plan-stem>/` derived from the plan’s `.md` filename.
4. **Reviewer:** Same active workspace as the task’s plan. Load reviewer model, single completion; chain-of-thought blocks (e.g. Qwen3 / DeepSeek-R1 *think* tags) are stripped, then any line starting with `APPROVED` / `REJECTED` is used for the verdict; state transitions.
5. **Recovery:** On startup, tasks stuck in `coding` / `review` reset to safe states.

## Model swapping

Only one large LLM should reside in VRAM at a time. `ModelManager.ensure_loaded` unloads other LLMs, runs the **memory gate** (wait until freed pages exceed a fraction of the unloaded model size), then calls `POST /api/v1/models/load`. LM Studio **guardrails** may block loads; the UI may allow “Load anyway” where the API does not—see README troubleshooting.

## Token discipline

Phases use **short system prompts** and **no Crew-style ReAct history** by default. Coder tool transcripts are truncated when long.
