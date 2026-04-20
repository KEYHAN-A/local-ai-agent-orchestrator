# Changelog

All notable changes to **Local AI Agent Orchestrator** are recorded here. For install and usage, see [README.md](README.md).

## Unreleased

(nothing yet)

## v3.1.1 — End-to-end agentic workflow (2026-04-19)

This release turns LAO from a plan-execution engine into a full ideation→DONE
agentic workflow driven by many small local models instead of one huge one.
Everything below is additive and gated behind settings; legacy plans with no
acceptance contracts degrade gracefully onto the v3.1.0 code path.

### New phases
- **Ideator + Spec Doctor** (`ideation.py`, `spec_doctor.py`): new pilot slash
  commands `/ideate <topic>`, `/lock`, `/spec`. The Ideator iterates with the
  user to produce `.lao/ideation/IDEATION.md`; the Spec Doctor then turns the
  locked draft into `SPEC.md` with numbered, machine-verifiable acceptance
  criteria (`AC-N`) and a risk register. Pilot also gains `/done [plan_id]`
  for a live DONE-gate report.
- **Contract Author** (`contract_author.py`): after the architect produces
  tasks with `acceptance_ids`, a small analyst-class model writes failing
  acceptance tests into `tests/acceptance/` and persists executable
  `acceptance.commands` on each task — TDD before any production code is
  written.
- **TDD inner repair loop** (`phases._coder_inner_repair_loop`): after the
  coder finishes, the acceptance runner is executed; on failure the coder is
  re-invoked with the test output until tests pass, bounded by
  `inner_repair_max_iterations` and `inner_repair_token_budget`. Repair count
  is persisted on the task.
- **Critic Quorum** (`critic_quorum.py`): N-model consensus review,
  quorum size auto-scaled from `task.risk` (low=1, med=3, high=5). Votes are
  aggregated and findings deduped by `(file_path, message)`; a quorum reject
  demotes the task back to rework with merged critical/major findings. Opt-in
  via `agentic.critic_quorum_enabled`.
- **Plan Integrator** (`plan_integrator.py`): when a plan passes the DONE
  gate, the integrator re-runs every task's acceptance commands across the
  whole plan (regression sweep), computes AC coverage (declared vs passing),
  appends a JSON record to `.lao/decisions.jsonl`, and writes a one-line
  summary into `LAO_MEMORY.md` so future runs see the decision in their
  system-prompt prelude.

### Definition of DONE
- **Acceptance runner** (`services/acceptance.py`): deterministic shell
  executor for contract-defined tests; results recorded as `acceptance`
  validation runs alongside existing lint/static checks.
- **Plan-level DONE gate** (`done_gate.py`): a plan is DONE only when
  (a) every task is `completed`, (b) every task with a contract has all
  commands green, (c) every declared `AC-N` has a passing run, (d) the
  critic quorum (when enabled) approves, (e) no outstanding critical
  findings and majors ≤ task budget, (f) no `BLOCKING` open questions
  remain in SPEC.md. Gate results persisted in the new `plan_done_gate`
  table. Legacy plans without contracts continue to close on the existing
  heuristic.

### State + schema
- New `MicroTask` fields (migrated in place): `acceptance: dict`,
  `risk: "low"|"med"|"high"`, `inner_repairs: int`, `critic_votes_json`.
- New `plan_done_gate` table with status + full report JSON.
- New CRUD helpers: `set_task_acceptance`, `set_task_risk`,
  `set_task_critic_votes`, `increment_inner_repairs`,
  `upsert_plan_done_gate`, `get_plan_done_gate`.

### Pilot UX + tool-call storm fix
- **`parallel_tool_calls: false`** is now sent on every pilot chat
  completion, with a `TypeError` fallback for older OpenAI clients. This is
  the real fix for the LM-Studio log showing 60 `file_read` calls streamed
  from a single Gemma response before the client could trim them — the model
  is now forced to emit one tool call per response, observe its result, then
  reason again.
- `PILOT_SYSTEM` gains a **"Tool-call discipline"** block telling the model
  to emit one call at a time, never re-read files it already read this
  conversation, and stop if it finds itself wanting to prefetch >3 files in a
  row.

### Settings (new `agentic:` block in `factory.yaml`)
```yaml
agentic:
  contract_author_enabled: true
  inner_repair_max_iterations: 3
  inner_repair_token_budget: 6000
  critic_quorum_enabled: false
  critic_quorum_size: 3
  critic_models: []
  critic_keep_reviewer_vote: true
  spec_doctor_enabled: true
  decision_log_enabled: true
  plan_integrator_enabled: true
```

### Prompts
- Extended `ARCHITECT_SYSTEM` + JSON schema: tasks now carry
  `acceptance`, `risk`, `token_budget_estimate`.
- New `CONTRACT_AUTHOR_SYSTEM`, `CRITIC_SYSTEM`, `IDEATION_SYSTEM`,
  `SPEC_DOCTOR_SYSTEM` plus matching message builders.

### Tests
- 305 tests pass (up from 235). New suites:
  `test_done_gate`, `test_contract_author`, `test_coder_inner_repair`,
  `test_critic_quorum`, `test_ideation_spec_doctor`, `test_plan_integrator`,
  plus `TestPilotParallelToolCallsDisabled` regression-guarding the
  tool-call storm fix.

## v3.1.0 — Reliability & Quality Overhaul (2026-04-19)

### Tier 1 — correctness & determinism
- **Tool contract refactor**: `tools.py` is now a `tools/` package. Every tool is a registered `Tool` dataclass carrying its own JSON-schema, permission callback, read-only / concurrency markers and prompt contribution; OpenAI schemas and the dispatch map are now built deterministically from the registry instead of dual-maintained dicts.
- **JSON-schema response mode**: `_llm_call` now accepts a `json_schema=` payload and uses `response_format={"type":"json_schema",…}` for architect / reviewer / analyst when the model advertises support; falls back to the existing regex parser otherwise.
- **Mechanical verifier phase**: a deterministic pre-reviewer pass (`verifier.py`) confirms claimed files exist, parse (Python / JSON / YAML), stay inside the workspace, and that the per-task TODO ledger is finished. Failures force a coder retry without consuming a reviewer attempt.
- **Per-task TODO ledger**: new `task_todos` SQLite table plus `task_todo_set` / `task_todo_get` tools. Coder prompt mandates publishing a checklist before the first `file_write`; verifier rejects unfinished items.
- **Permission system**: new `permissions.py` with modes (`auto`, `confirm`, `plan_only`, `bypass`) and Claude-Code-style wildcard rules (`Bash(git *)`, `FileWrite(/src/*)`). Wired into every tool dispatch with audit trail in the new `tool_audit` table; exposed via `lao run --permission-mode`.

### Tier 2 — guidance & memory
- **Plan mode**: first-class `enter_plan_mode` / `exit_plan_mode` tools; the permission layer blocks mutating tools until the user explicitly approves the proposed plan.
- **Skills system**: `skills/` package with bundled `verify`, `stuck`, `simplify`, `write_tests`, `refactor_safe`, `security_audit`, `repro_bug` skills. `skill_list` / `skill_run` / `skill_clear` tools, plus `lao skills [list|show <name>]` CLI.
- **Hierarchical memory**: project-scoped `LAO_MEMORY.md` and user-global `~/.lao/MEMORY.md`. New `memory_read` / `memory_append` / `memory_forget` tools, automatic post-approval extraction in `services/extract_memories.py`, and `lao memory [show|edit|forget]` CLI.
- **Conversation compaction**: `services/compact.py` replaces naive `[system] + last_16` trims with a system-preserving compactor that summarizes the dropped middle (LLM summarizer optional, character-budget fallback by default).
- **Speculative coder retries via git worktrees**: `worktrees.py` provides `attempt_worktree(...)` for isolated coder attempts, gated by `git.worktrees: true`.

### Tier 3 — extensibility & observability
- **MCP integration**: `services/mcp_client.py` auto-registers external MCP servers declared in `factory.yaml: mcp_servers` as `mcp__<server>__<tool>` tools. `lao mcp-server` exposes a curated subset of LAO tools to other agents over stdio.
- **Sub-agents**: new `agent_run(goal, allowed_tools, max_rounds)` tool runs an ephemeral, read-only subagent for bounded investigations from the pilot.
- **Per-role determinism knobs**: `temperature`, `top_p`, `seed`, `repetition_penalty` are now per-role on `ModelConfig`; reviewer defaults to `temperature: 0.0`. New `--seed` flag overrides every role for the run.
- **`lao doctor`**: grouped diagnostics (LM Studio, models, RAM budget, git, embedder, validators, disk, schema) with `ok | warn | fail` per row and remediation hints.
- **Hooks framework**: `hooks_registry.py` discovers `<config_dir>/hooks.py` and exposes `pre_tool` / `post_tool` / `pre_phase` / `post_phase` extension points.
- **OpenTelemetry exporter**: optional, lazy-loaded (`services/otel.py`) — wraps every tool call in a span when `LAO_OTEL_ENDPOINT` or `otel.enabled` is set. New `output_style: terse|narrative|json` knob shapes free-form replies across phases via `--output-style`.

### Tests & docs
- New tests: `test_tool_contract`, `test_permissions`, `test_skills`, `test_memory`, `test_compact`, `test_verifier_phase`, `test_plan_mode`.
- Suite green: 235 tests pass.
- README, GitHub Pages (`docs/index.html`), `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, and `factory.example.yaml` updated for the new configuration surface.

## v3.0.12 — Fix analyst role missing from init wizard

- **Fix:** `lao init` raised `KeyError: 'analyst'` when the user chose manual model keys — `analyst` was not included in `_default_model_profiles()` for any tier.
- Added `analyst` to all three profiles (`small`, `medium`, `large`) with sensible defaults (`qwen2.5-7b-instruct` for small/medium, `qwen2.5-14b-instruct` for large).
- Added `analyst` to the Step 1 role guide table in the init wizard.

## v3.0.11 — Analyst agent, P0 bug fixes, cross-platform memory gate, resilience

### Analyst agent (Phase 0)
- **New `analyst_phase()`** in `phases.py`: read-only workspace survey using a small model with a large context window. Runs before the architect and writes `analyst_report.json` + `ANALYST.md` to the plan workspace. Idempotent — reuses existing report on resume.
- **`analyst.py`**: tiered input builder assembles directory tree → manifests → import summary → source excerpts → plan-referenced files within the model's token budget (`context_length * max_context_utilization - max_completion`).
- **Analyst context injected** into architect and reviewer prompts (summary, risk areas, integration points, build system).
- **`analyst` model role** added to `settings.py`, `factory.example.yaml`, `configure-models` interactive flow.
- **`--analyst-model`** and **`--no-analyst`** CLI flags; **`orchestration.analyst_enabled`** YAML setting (default `true`).
- **Quality report** (`quality_report.json` / `LAO_QUALITY.md`) now records whether an analyst report was generated.

### P0 bug fixes
- **`model_manager.py`**: read `page_size` from the `vm_stat` header line instead of hardcoding 16 384 — fixes incorrect memory gate calculations on non-M-series Macs.
- **`phases.py`**: architect `log_run` now records the real `chunk_duration` instead of `0.0`.
- **`state.py`**: removed dead `'rework'` status from `has_pending_work` query (status was renamed; caused false "work pending" signals).
- **`kpi.py`**: normalize `strict_closure_allowed_statuses` (lowercase + strip) consistently with `reporting.py`.

### P1 resilience
- **`history.py`**: atomic JSON writes (temp file + `os.replace`); corrupt or non-list files are backed up as `.bak` with a warning log instead of silently discarded.
- **`consistency.py`**: `OSError` guard on `read_text` so unreadable files are skipped rather than crashing the check.
- **`tools.py`**: `file_patch` uses `errors="replace"` encoding to handle non-UTF-8 files gracefully.
- **`reporting.py`**, **`kpi.py`**, **`dashboards.py`**: all JSON snapshot writes are now atomic.

### P2 cross-platform
- **`model_manager.py`**: Linux `/proc/meminfo` (`MemAvailable`) fallback for `_get_available_memory_bytes` and `_get_swap_used_bytes`. Other platforms fail open (gate skipped with a debug log).

### Documentation
- **`docs/ARCHITECTURE.md`**: fully rewritten — all modules listed in categorised tables, Phase 0 (analyst) documented in execution flow, contributor guides for adding new phases and workspace tools.

## v3.0.10 — Model swap observability and throughput docs

- **Observability:** TTY **LAO run finished** report and plain **`lao run`** factory status now separate **run-log model_key changes** (SQLite `run_log`) from **LM Studio swap cycles**, **loads**, and **unloads** (`ModelManager` metrics).
- **Reporting:** **`LAO_QUALITY.md`** adds a **Model loading (this LAO process)** section when LM Studio metrics are included in **`quality_report.json`** → **`efficiency`**.
- **Docs:** README **Throughput and LM Studio model swaps** (shared role keys, `phase_gated` / batch sizes); ARCHITECTURE **Model swapping** expanded with batching and observability pointers.

## v3.0.9 — Validation inference, LAO_QUALITY.md, Pilot /gates, reviewer rubrics

- **Validation inference:** `orchestration.infer_validation_commands` (default **true**) suggests conservative **build** / **lint** commands from common manifests (`package.json`, `pyproject.toml` / `setup.py`, `go.mod`, `Cargo.toml`, `Package.swift`); merged into `run_optional_validation_commands` when profile slots for `build` / `lint` are free and explicit `validation_build_cmd` / `validation_lint_cmd` are unset (with command-string dedupe).
- **Reporting:** `quality_report.json` includes `validation_inference`; **`LAO_QUALITY.md`** is written beside it as a short human summary.
- **Reviewer:** Task-keyword rubric hints appended in `build_reviewer_messages` for API/HTTP, database, security, UI, and CLI-style tasks.
- **Pilot:** **`gate_summary`** tool and **`/gates`** slash command (optional plan ref); `factory.example.yaml` and `lao init` templates document the flag; commented optional host **security** profile example (semgrep/bandit).
- **Docs:** **CONFIGURATION.md** covers inference, `LAO_QUALITY.md`, `/gates`, and optional security profiles.
- **Tests:** Validators, reporting, pilot, pilot tools, and prompt rubric coverage.

## v3.0.8 — Pilot workspace, Swift validation, plan ergonomics

- **Pilot Mode:** Tools (`list_dir`, `file_read`, `shell_exec`, search) bind to `config_dir` or the newest plan folder with actionable tasks; fixed `ContextVar` misuse when switching projects via `/project`.
- **`create_plan`:** Strip trailing `.md` from titles so filenames stay readable (e.g. `FIX_AUTH_MANAGER.md` → `FIX_AUTH_MANAGER.md` on disk, not `FIX_AUTH_MANAGERmd.md`).
- **`pipeline_status` / `retry_failed`:** Plans show `id=`, `file=`, and `workspace=`; `retry_failed` accepts plan filename or stem via `resolve_plan_ref`.
- **Swift validation:** Comment/string stripping before `: Any` / `[String: Any]` heuristics; regex-based checks; Codable+`[String: Any]` scan uses stripped Swift text; optional **minor** `missing_ios_manifest` when Swift exists without root `Package.swift` / `.xcodeproj`.
- **Architect:** Unknown task dependencies log **difflib** “similar title” hints instead of a single combined line.
- **Config / docs:** `swift_ios` validation profile in defaults and `factory.example.yaml`; **CONFIGURATION.md** documents Swift contract, Xcode/SPM `validation_build_cmd` examples, and pilot/retry behavior.
- **Repository:** Removed orphaned `example_plan` submodule gitlink (no `.gitmodules` entry).

## v3.0.7 — README hero screenshot & home menu asset

- **README:** Home menu image moved to the top (under the title); duplicate screenshot removed from the Pilot section; refreshed **`docs/assets/lao-home-menu.png`** for PyPI and GitHub rendering.

## v3.0.6 — README & site: installation up front

- **README:** **Installation** is first in the table of contents and appears immediately after the TOC (before **Features**).
- **Site:** Meta description and hero copy foreground install paths (`pip` + link to the curl installer); housekeeping version strings to **v3.0.6**.

## v3.0.5 — Install scripting & distribution docs

- **Install:** `scripts/install.sh` (prefers **pipx**, else `pip install --user`) and optional env `LAO_VERSION` / `LAO_PACKAGE`.
- **Site:** `docs/install.sh` bootstrap for [lao.keyhan.info/install.sh](https://lao.keyhan.info/install.sh) (delegates to the canonical script on GitHub).
- **Docs:** README guidance for curl-based install (trust trade-offs), Homebrew via **pipx**, and why npm is not used for this Python CLI.

## v3.0.4 — LAO Pilot Mode

This release highlights **Pilot Mode**: an interactive local-LLM chat that runs workspace tools, inspects pipeline status, creates plans, and hands control back to the autopilot pipeline when you are ready.

- **Pilot Mode:** Full-screen unified CLI (`UnifiedUI` + prompt_toolkit) for continuous scrollback, status bar, and robust terminal rendering (banner outside `patch_stdout` to avoid ANSI mangling).
- **Project-aware Pilot:** `ProjectRegistry` (`~/.lao/projects.json`), `lao projects` subcommands, `/project` in chat, intent-based project resolution, tool-loop bailout after repeated errors, and clearer empty-LLM fallbacks.
- **Slash vs path:** Absolute paths (e.g. `/Users/...`) are no longer mistaken for slash commands.
- **Home menu UX:** Primary actions grouped (Initialize workspace + Pilot), then other actions with `Exit` last; default focus on **Initialize workspace** (home-root safety still defaults to Exit).
- **Branding:** Updated block-style **LAO** ASCII logo on splash and pilot banner.
- **Input:** Double **Ctrl+C** exits pilot prompt; single Ctrl+C nudges to press again or keep typing.
- **Docs & site:** README and [lao.keyhan.info](https://lao.keyhan.info) (`docs/index.html`) updated with Pilot workflow, terminal screenshots under `docs/assets/`, and expanded architecture/configuration notes.

## v2.3.0

- **Release rollout:** publish preparation for `v2.3.0` with project metadata version bump and release packaging flow.
- **Orchestrator baseline:** ships current LAO pipeline state as the next tagged version for distribution.

## v2.2.1

- **Reviewer parsing robustness:** reviewer JSON validation now accepts markdown-fenced JSON and mixed-text wrappers, preventing false task rejection when verdict payloads are wrapped in JSON code fences.
- **Regression coverage:** added validator tests for fenced reviewer responses so `APPROVED` verdicts are correctly recognized.

## v2.2.0

- **Pipeline completion correctness:** plans now move to `completed` when all tasks reach a terminal state (`completed` or `failed`) instead of staying `active`.
- **Recovery ergonomics:** added `lao retry-failed` (with `reset-failed` alias) backed by queue API support to quickly re-run failed tasks.
- **Scheduler robustness:** pending tasks that depend on failed prerequisites are now auto-failed with explicit dependency-block feedback.
- **Reviewer quality gate tuning:** reviewer guidance and parsing now treat minor-only feedback as non-blocking, reducing over-rejection loops on local models.
- **Test coverage:** added tests for terminal-plan detection, failed-task reset, failed-dependency scheduling behavior, and reviewer minor-finding approval behavior.

## v2.1.1

- **Website metadata:** replaced hardcoded site version text with live GitHub/PyPI badges and direct latest-release links.
- **Publish workflow reliability:** release-triggered PyPI automation now avoids duplicate tag/release uploads while keeping manual re-run support.

## v2.1.0

- **Orchestration quality:** planner chunk-resume preflight, dependency-aware scheduling, role-batched coder/reviewer waves, and structured findings storage.
- **Production gates:** validator framework for placeholder/schema/project-integrity checks, configurable quality gates, and per-plan `quality_report.json` traceability.
- **Release automation:** GitHub Actions workflow added for automated PyPI publish on release/tag with Trusted Publishing support.

## v2.0.0

- **Unified UX:** `lao`, `lao init`, and `lao configure-models` now match the polished `lao run` visual language with guided, step-based TTY flows.
- **Operator continuity:** post-setup/post-config next actions (`health`/`run`) are integrated, reducing dead ends between commands.
- **Recovery clarity:** startup checks and model-remap guidance are surfaced as first-class interactive flows for smoother long-running operation.

## v1.3.0

- **Git:** Optional per-plan repo under **`./<plan-stem>/`**: snapshot **`LAO_PLAN.md`**, **`LAO_TASKS.json`** after architect, commits after coder/reviewer with **`lao(…):`** messages; **`LAO_REVIEW.log`** for review outcomes. Config **`git:`** in `factory.yaml`; CLI **`--no-git`**.
- **Site:** Redesigned GitHub Pages landing (hero, features, install).

## v1.2.0

- **Layout:** Per-plan code lives at **`./<plan-stem>/`** next to `plans/` (not under `.lao/workspaces/`). Fallback workspace **`.lao/_misc/`**.
- **CLI:** Rich **full-screen dashboard** on TTY (`--plain` for classic log). **`lao init`** adds workspace **`README.md`** when missing (`--skip-readme` to skip).
- **Fix:** SQLite no longer opens a bogus **`None`** database file when using the default `TaskQueue()` constructor.
- **Branding:** LAO palette on CLI, site, and docs.

## v1.1.1

- **Layout (superseded in v1.2.0):** `lao init` created **`.lao/workspaces/`** + **`plans/`**; per-plan workspace was **`.lao/workspaces/<plan-stem>/`** (from `plans/Foo.md` → `Foo`).
- **Plans:** Ignore **`plans/README.md`** when scanning for plans.
- **Defaults:** Reviewer model default **deepseek-r1-distill-qwen-32b** (adjust `key` to match `lms ls`).
- **Docs:** [docs/PYPI_PUBLISH.md](docs/PYPI_PUBLISH.md); local token notes template **`PYPI_PUBLISH.local.md`** (gitignored).

## v1.1.0

- **Reviewer:** Strip chain-of-thought (*think* tags) and detect `APPROVED` / `REJECTED` on any line — fixes false rejections from DeepSeek-R1–style reasoning before the verdict.
- Docs and landing page updated for this release.

## v1.0.0

- Initial stable release: `lao` CLI, planner / coder / reviewer pipeline, SQLite state, memory gate, GitHub Pages docs.
