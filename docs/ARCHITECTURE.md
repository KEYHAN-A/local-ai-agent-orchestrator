# Architecture

## Components

### Pipeline core

| Module | Role |
|--------|------|
| `cli.py` | Command routing, interactive home (`lao`), `init`, `configure-models`, **`lao pilot`**, **`lao projects`**, settings bootstrap |
| `runner.py` | Main loop: plan ingestion, task dispatch, signals; wires **UnifiedUI** + **PilotAgent** when idle |
| `phases.py` | All four pipeline phases: `analyst_phase`, `architect_phase`, `coder_phase`, `reviewer_phase` + shared OpenAI client helpers |
| `analyst.py` | Tiered workspace input assembly (`build_analyst_input`) and JSON report parser for the analyst phase |
| `state.py` | SQLite WAL: `plans`, `micro_tasks`, `run_log`, `task_findings`, `plan_chunks`, `plan_deliverables`, `task_validation_runs`, `pilot_conversations` |
| `settings.py` | Runtime configuration (YAML + env + CLI overrides); all six model roles including `analyst` |
| `model_manager.py` | LM Studio REST `load` / `unload`, memory gate (macOS `vm_stat` + Linux `/proc/meminfo`), JIT fallback |
| `prompts.py` | System prompts and message builders for all phases: `ANALYST_SYSTEM`, `ARCHITECT_SYSTEM`, `CODER_SYSTEM`, `REVIEWER_SYSTEM`, `PILOT_SYSTEM` |
| `tools.py` | Filesystem + shell + embedding search tools; `TOOL_SCHEMAS` / `TOOL_DISPATCH` for coder and pilot |
| `plan_git.py` | Optional per-plan Git: `git init`, plan snapshot, phase commits (`lao(plan|architect|coder|reviewer): …`) |

### Validation and quality

| Module | Role |
|--------|------|
| `validators.py` | Post-coder gate: placeholder scan, Swift Codable, schema lints, registered analyzers, cross-file consistency, optional build/lint commands, reviewer JSON parsing, language inference |
| `analyzers.py` | Per-file analyzer registry: Python `py_compile`, TypeScript delimiter heuristic, JSON structure check |
| `schema_lints.py` | Reusable Swift/TS/JS/Py schema-safety lints (`[String: Any]`, `: Any`); imported by `validators.py` |
| `consistency.py` | Cross-file reference check: quoted path strings that do not exist in the workspace |
| `repair.py` | Deterministic repair feedback builder from structured `Finding` lists; code signature for no-progress detection |

### Reporting and observability

| Module | Role |
|--------|------|
| `reporting.py` | Plan-level quality report: `quality_report.json` + `LAO_QUALITY.md`; includes analyst, efficiency, traceability, convergence, observability sections |
| `benchmarks.py` | Synthetic reliability benchmark suite; writes `benchmark_report.json` |
| `dashboards.py` | Operator dashboard snapshot across all plans; writes `dashboard_snapshot.json` |
| `kpi.py` | Cross-plan KPI snapshot (plan success rate, first-pass rate, token efficiency, …); writes `kpi_snapshot.json` |
| `history.py` | Append-only JSON history for trend tracking (atomic write, corrupt-file backup) |
| `report_schema.py` | Quality report schema version constants and shallow migration helpers |

### UI and interaction

| Module | Role |
|--------|------|
| `unified_ui.py` | **Unified TTY shell** for pilot + runner: `TerminalCapabilities`, `RenderBus`, `ViewComposer`, `TerminalShell` (Rich + **prompt_toolkit**), `LogBridge` |
| `interactive_ui.py` | Shared Rich primitives for headers, status tables, prompts, guided menus, **questionary** selects |
| `console_ui.py` | Legacy/plain helpers; superseded by unified UI on TTY |
| `pilot.py` | **PilotAgent** chat loop, tool dispatch, slash commands, project intent/resolver, consecutive tool-error guard |
| `pilot_tools.py` | Pilot-only tools (`create_plan`, `pipeline_status`, `retry_failed`, `resume_pipeline`, `gate_summary`, …) + workspace tool schemas |
| `pilot_ui.py` | Pilot mode UI entry helpers |
| `project_registry.py` | **ProjectRegistry** / `ProjectEntry`: scan/add/list/`~/.lao/projects.json` persistence |
| `branding.py` | ASCII logo and version string |

## Execution flow

1. **Operator entry:** `lao` presents environment readiness and grouped next actions (`init`, **Pilot**, `run`, **`projects`**, scan, …). **`lao pilot`** or **`lao projects`** bypass the home menu.
2. **Idle / Pilot:** When `orchestration.pilot_mode_enabled` is true (default), **`lao run`** on a TTY hands off to **PilotAgent** after pipeline work drains; **`TaskQueue`** is bound for tools; **`/resume`** or **`resume_pipeline`** returns to the autopilot loop. **UnifiedUI** renders scrollback, status bar, and prompt (**double Ctrl+C** exits the prompt).
3. **Plans:** New `.md` files under `plans/` (or `--plan`) are hashed; new content gets a `plan_id` and analyst + architect run.
4. **Analyst (Phase 0):** When `orchestration.analyst_enabled` is true (default), the analyst model surveys the workspace using a tiered snapshot (directory tree → manifests → import summary → source excerpts → plan-referenced files) within its token budget. Output is a structured JSON report (`analyst_report.json` + `ANALYST.md`). The phase is idempotent: if the report already exists it is reused. Disable with `--no-analyst` or `orchestration.analyst_enabled: false`.
5. **Architect (Phase 1):** The plan text (plus analyst summary if available) may be **split into chunks** to fit the planner context. Each chunk is sent to the planner model; output is parsed as a **JSON array** of micro-tasks. Chunk results are merged, deduplicated at the plan level, and persisted to SQLite. Completed chunks can be **skipped on resume** if already stored.
6. **Scheduler:** With **`phase_gated`**, the runner may batch **coder** work then **reviewer** work in waves (`coder_batch_size`, `reviewer_batch_size`). Otherwise it alternates coder → reviewer per task. Dependencies constrain which **pending** tasks are eligible.
7. **Coder (Phase 2):** For each pending task, load coder model, optional embedding retrieval (`find_relevant_files`), tool loop until final message. File tools run inside **`use_plan_workspace`**: **`<config_dir>/<plan-stem>/`** derived from the plan's `.md` filename.
8. **Reviewer (Phase 3):** Same active workspace as the task's plan. Load reviewer model, single completion; chain-of-thought blocks (e.g. Qwen3 / DeepSeek-R1 *think* tags) are stripped, then output is parsed as **structured JSON** (`verdict`, `findings`, `summary`)—including JSON inside **markdown fences** or embedded in prose. Optional static **validators** may add findings and reject before the LLM review when quality gates are strict enough. State transitions: completed, rework (with feedback), or failed after max attempts. Analyst context (integration points, risk areas) is injected into the reviewer prompt when available.
9. **Recovery:** On startup, tasks stuck in `coding` / `review` reset to safe states.

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

Only one large LLM should reside in VRAM at a time. `ModelManager.ensure_loaded` unloads other LLMs, runs the **memory gate** (wait until freed pages exceed a fraction of the unloaded model size), then calls `POST /api/v1/models/load`. LM Studio **guardrails** may block loads; the UI may allow "Load anyway" where the API does not—see README troubleshooting.

**When a swap actually happens:** `ensure_loaded` is a no-op if the role's configured model `key` is already loaded. Mapping **the same LM Studio `key`** for `planner`, `coder`, and `reviewer` avoids cross-role unload/load cycles entirely (the embedder is small and may stay loaded alongside).

**Batching:** With **`orchestration.phase_gated`** (default), the runner processes up to **`coder_batch_size`** coding tasks, then up to **`reviewer_batch_size`** reviews, before switching roles again—so distinct role keys incur fewer swaps per N tasks than **`phase_gated: false`**, which alternates coder → reviewer every task.

**Observability:** `ModelManager` tracks **`swap_count`**, **`load_count`**, and **`unload_count`** for the current process. After a TTY run, the **LAO run finished** report contrasts **run-log model_key changes** (SQLite `run_log`: successive rows with different `model_key`) with **LM Studio swap cycles** (real unload-then-load). The same fields appear under **`efficiency`** in **`quality_report.json`** and **Model loading** in **`LAO_QUALITY.md`** when reports are written.

**Memory gate platforms:** macOS uses `vm_stat` (free + inactive + purgeable pages); Linux uses `/proc/meminfo` (`MemAvailable`). Other platforms fail open (gate skipped).

## Token discipline

Phases use **short system prompts** and **no Crew-style ReAct history** by default. Coder tool transcripts are truncated when long. The analyst phase uses a tiered input builder that stays within `context_length * max_context_utilization - max_completion` tokens.

## Adding a new phase

1. Add a `ModelConfig` entry to `_default_models()` in `settings.py` and the `role_map` dict.
2. Add the model to `factory.example.yaml` under `models:`.
3. Add a system prompt constant and `build_<phase>_messages()` in `prompts.py`.
4. Implement `<phase>_phase(mm, queue, ...)` in `phases.py` following the pattern:
   - `mm.ensure_loaded("<role>")` → `_get_client()` → `_llm_call(...)` → parse output → `queue.log_run(...)`.
5. Wire the phase into `runner.py` (`run_factory` and/or `run_entry`).
6. Add `--<phase>-model` CLI flag and optional `--no-<phase>` toggle in `cli.py`.

## Adding a new workspace tool

`tools.py` is now a `tools/` package. Each tool is a `Tool` dataclass registered into a single module-level registry; the OpenAI schema list and dispatch map are derived from the registry, so there is no longer any dual maintenance.

1. Pick the right submodule under `src/local_ai_agent_orchestrator/tools/` (`fs.py`, `shell.py`, `search.py`, `todos.py`, `plan_mode.py`, `skills_tools.py`, `memory_tools.py`, `subagent.py`) — or create a new one.
2. Implement the function. Return a string (or JSON-serializable value); use `tools.meta.resolve_path` for filesystem operations.
3. Build a `Tool(...)` and pass it to `register(...)`. Set `is_read_only`, `is_concurrency_safe`, `plan_mode_safe`, and `check_permissions` accordingly.
4. If the tool should be visible to the coder, add its name to `_coder_tool_names()` in `tools/__init__.py`.
5. For pilot-only tools, add to `PILOT_TOOL_SCHEMAS` / `PILOT_TOOL_DISPATCH` in `pilot_tools.py` instead.

## Reliability subsystems (Unreleased)

```
runner.run_factory
  ├─ analyst_phase
  ├─ architect_phase                  ← uses _llm_call(json_schema=ARCHITECT_JSON_SCHEMA)
  ├─ for each task:
  │    coder_phase                    ← _dispatch_tool_call → permissions → audit → hooks → otel span
  │    verifier_phase  (NEW)          ← file existence, AST/JSON parse, TODO ledger
  │    reviewer_phase                 ← uses _llm_call(json_schema=REVIEWER_JSON_SCHEMA)
  │    extract_memories  (post-approval) → LAO_MEMORY.md / ~/.lao/MEMORY.md
  └─ pilot_loop (idle)
```

Cross-cutting modules:

- `permissions.py` — mode + wildcard rules; consulted in every tool dispatch; results land in the new `tool_audit` SQLite table.
- `services/compact.py` — system-preserving message compaction with optional LLM summarizer.
- `verifier.py` — mechanical checks; failures force coder retry without consuming reviewer attempts.
- `skills/` and `services/memory.py` — both contribute prompt addenda via `prompts._augment_system`.
- `hooks_registry.py` — discovers `<config_dir>/hooks.py` and exposes `pre_tool` / `post_tool` / `pre_phase` / `post_phase`.
- `services/mcp_client.py` & `services/mcp_server.py` — MCP integration, both directions.
- `worktrees.py` — speculative coder attempts inside isolated `git worktree` directories (gated by `git.worktrees`).
- `services/otel.py` — lazy-loaded OpenTelemetry exporter; wraps every tool call in a span when an endpoint is configured.
- `doctor.py` — backs `lao doctor`; safe to run even without a live LM Studio.
