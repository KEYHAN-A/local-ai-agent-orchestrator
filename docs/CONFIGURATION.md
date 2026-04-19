# Configuration

## LM Studio prerequisite

LAO’s default path assumes **[LM Studio](https://lmstudio.ai/)** is installed, the **local server** is running (same host/port as **`lm_studio_base_url`** in `factory.yaml`, often `http://127.0.0.1:1234`), and model **keys** match the server. **Memory-aware switching** (`ModelManager`) calls LM Studio’s REST **load** and **unload** endpoints so only one large LLM tends to sit in VRAM at a time; other OpenAI-compatible servers may work for chat but not for that automation. See the README **Prerequisites (LM Studio)** section for the first-run checklist.

## factory.yaml

Resolved paths under `paths` are **relative to the YAML file’s directory**.

Top-level keys:

| Key | Description |
|-----|-------------|
| `lm_studio_base_url` | LM Studio server root (no trailing `/v1`) |
| `openai_api_key` | Dummy value for LM Studio (e.g. `lm-studio`) |
| `total_ram_gb` | Optional; logged at startup; reserved for future heuristics |
| `paths.plans` | Incoming `.md` plans |
| `paths.database` | SQLite path (recommended: `./.lao/state.db`) |
| `paths.workspace` | Optional fallback when no per-plan context is active (default: `.lao/_misc`). Coder tasks use each plan’s folder under `config_dir`; **Pilot Mode** binds `list_dir` / `file_read` / `shell_exec` to the config directory or the newest plan folder that still has pending/failed work. |
| `memory_gate.*` | `release_fraction`, `swap_growth_limit_mb`, `settle_timeout_s`, `poll_interval_s` |
| `orchestration.*` | Timeouts, retries, `max_task_attempts`, `plan_watch_interval_s`, **`pilot_mode_enabled`** (default true: TTY run hands off to Pilot when idle), **`pilot_context_lines`**, etc. |
| `git.enabled` | When **true** (default), run Git commits in each **per-plan project folder** (`./<plan-stem>/`). Requires **`git`** on `PATH` and committer identity. Override with CLI **`--no-git`**. |
| `git.plan_file_name` | Snapshot filename for the plan markdown (default **`LAO_PLAN.md`**) |
| `git.commit_trailers` | When **true**, add **`LAO-Plan-ID`** / **`LAO-Task-ID`** lines to the commit body (second `-m` paragraph) |
| `models.<role>` | `key`, `context_length`, `max_completion`, `supports_tools`, `size_bytes`, `description` |

Roles: `planner`, `coder`, `reviewer`, `embedder`, **`pilot`** (**Pilot Mode** chat agent; configure `key` / context like other roles).

**Pilot (`models.pilot`):** Used when you run **`lao pilot`**, from the interactive home menu, or when **`lao run`** enters Pilot while idle. Match the model key to LM Studio (`lao health` / `lms ls`).

**Planner (`models.planner`):** Large markdown plans need a high **`context_length`** so the full plan fits in the prompt. The architect emits a **JSON array of micro-tasks**, which can be long — set **`max_completion`** high as well (defaults in `factory.example.yaml` use `32768` / `16384`). If you see truncated JSON or `finish_reason=length` errors, increase both values in **`factory.yaml`** and reload the model in LM Studio with the same context size.

**Reviewer (`models.reviewer`):** Reasoning models (e.g. DeepSeek-R1 distill) may emit *think*-tagged chain-of-thought before `APPROVED` or `REJECTED: …`. As of **v1.1.0**, the orchestrator strips those blocks and scans line-by-line for the verdict, so you only need a valid **`key`** and appropriate **`context_length`** / **`max_completion`** in YAML.

### Per-plan project folders (v1.2.0+)

- Code for `plans/MyFeature.md` is written under **`<factory.yaml directory>/MyFeature/`** (same stem as the plan filename, next to `plans/`).
- **`README.md`** in `plans/` is **ignored** when scanning for new plans (so it is not decomposed as a project plan).
- **`lao init`** creates **`.lao/`**, **`plans/`**, an optional root **`README.md`** (workspace guide), and **`factory.example.yaml`**.
- In interactive mode, **`lao init`** also writes a ready-to-run **`factory.yaml`** using guided prompts.
- SQLite and WAL files belong under **`.lao/`** when using the example `paths.database` — keeps stray `NANO*` / WAL files out of the repo root.

### Project registry (v3.0.4+)

- **`lao projects`** stores known LAO workspaces in **`~/.lao/projects.json`** (JSON file, no extra DB).
- Use **`scan`** to discover directories under a root that contain `factory.yaml`, `plans/*.md`, or **`.lao/state.db`**.
- **`use`** / **`add`** register a path; **`needs-action`** surfaces queues with pending/failed work.
- In **Pilot**, **`/project list`**, **`/project scan`**, **`/project use <name>`**, **`/project status`** hit the same registry.

### Interactive setup and recovery

- Running bare **`lao`** (TTY) opens an interactive home flow that shows environment status and grouped next actions (**Initialize workspace**, **Pilot**, then other actions including **`run`**, **`projects`**, **`health`**, **`configure-models`**).
- The interactive commands share a consistent visual system (Rich header panels, status tables, guided step prompts), so setup and run phases feel continuous.
- If startup is blocked by missing models, run **`lao configure-models`** to remap role keys (`planner`, `coder`, `reviewer`, `embedder`) without manually editing YAML.
- After `lao init` or `lao configure-models`, LAO can immediately chain to next actions (`health` or `run`) from the same guided flow.

### Continue / resume behavior

- LAO persists progress in SQLite (`paths.database`, default `./.lao/state.db`).
- Restarting **`lao run`** resumes pending/coded work from queue state.
- Interrupted transient states are automatically recovered on startup:
  - `coding` -> `pending`
  - `review` -> `coded`
- Plan identity is based on plan content hash. Reusing the exact same plan content is recognized as already registered; edited content creates a new plan identity.

### Existing project workflow

- Plan `plans/MyRepo.md` maps to workspace folder `./MyRepo/` (same stem).
- If `./MyRepo/` already exists (for example a cloned repo), LAO will operate there.
- This allows an existing codebase workflow by intentionally aligning plan filename stem and target folder name.

### Discovering model `key` values

- LM Studio CLI: `lms ls`
- HTTP: `GET http://127.0.0.1:1234/v1/models`

`size_bytes` should match on-disk size from `lms ls` (used for memory-gate bookkeeping).

### Swift / iOS validation contract

- Static checks flag **untyped** `: Any` and `[String: Any]` in `.swift` (comments and string literals are stripped first). Plans should tell the coder to use concrete `Codable` types, tagged enums, or `Data` + `JSONDecoder` instead.
- Set **`orchestration.validation_profile: swift_ios`** when working on Apple platforms; the profile matches **`default`** today and exists as a documented convention—pair it with real build/lint commands.
- After **`Package.swift`** or an **`.xcodeproj`** exists in the per-plan workspace, set **`validation_build_cmd`** and optionally **`validation_lint_cmd`** so the reviewer’s gate runs your toolchain (see commented examples in **`factory.example.yaml`**).

Example **`validation_build_cmd`** (adjust scheme and simulator):

```bash
xcodebuild -scheme YourApp -destination 'platform=iOS Simulator,name=iPhone 16' build
```

Example Swift Package build from the plan folder:

```bash
swift build
```

When Swift sources exist but no manifest is at the workspace root, LAO emits a **minor** advisory (`missing_ios_manifest`) so you know the tree may not compile yet.

### Inferred validation commands

- **`orchestration.infer_validation_commands`** (default **true**): when the active **`validation_profiles[profile].commands`** list does not already include **`kind: build`** or **`kind: lint`**, and **`validation_build_cmd`** / **`validation_lint_cmd`** are unset, LAO infers conservative commands from common manifests in the **per-plan workspace** (`package.json` scripts, Python **`pyproject.toml`** / tests, **`go.mod`**, **`Cargo.toml`**, **`Package.swift`**).
- Inference is **best-effort** and **host-only** (your machine must have the tools). Disable with **`infer_validation_commands: false`** if you want only explicit YAML commands.
- **`quality_report.json`** and **`LAO_QUALITY.md`** (same folder) include a **`validation_inference`** snapshot with suggested commands. Pilot exposes the same via the **`gate_summary`** tool and **`/gates`** (optional plan id / filename after the command).

### Optional security-oriented profile

Host scanners can be wired like any other profile command (see commented **`security_host`** example in **`factory.example.yaml`**). LAO does not install these tools; they must be on your **`PATH`**.

### Pilot tools and `retry_failed`

- **`pipeline_status`** lists each plan’s internal **`id=`**, **`file=`**, and **`workspace=`** path. **`retry_failed`** accepts that **id**, the **filename** (e.g. `MyPlan.md`), or the stem (`MyPlan`).
- Creating plans via Pilot: titles may end with **`.md`**; it is stripped so filenames stay readable.

## Environment variables

See [.env.example](../.env.example). `LAO_CONFIG` can point to an absolute path to your yaml.

## CLI overrides

CLI flags override YAML after merge. Example:

```bash
lao --lm-studio-url http://192.168.1.10:1234 --ram-gb 64 \
  --reviewer-model my-reviewer-mlx run
```

Pilot-related flags (see **`lao run --help`**): **`--no-pilot`** disables idle Pilot on TTY; **`--pilot-only`** opens Pilot immediately (same path as **`lao pilot`**). **`--pilot-model`** overrides the pilot role key.

## Reliability & Quality knobs (Unreleased)

| Setting | Type | Default | Notes |
|---|---|---|---|
| `permissions.mode` | `auto \| confirm \| plan_only \| bypass` | `auto` | `--permission-mode` overrides per run. |
| `permissions.allow` / `permissions.deny` | list of rules | `[]` | Wildcards: `Bash(git *)`, `FileWrite(/src/*)`, … |
| `verifier_enabled` | bool | `true` | Mechanical pre-reviewer pass (file existence, AST/JSON parse, TODO ledger). |
| `compaction_enabled` / `compaction_keep_recent` | bool / int | `true` / `8` | Replaces the legacy `last 16` trim. |
| `skills_enabled` / `skills_dirs` | bool / list | `true` / `[]` | Bundled skills always load; user dirs append. |
| `memory_enabled`, `memory_project_filename`, `memory_user_path` | bool / str | `true`, `LAO_MEMORY.md`, `~/.lao/MEMORY.md` | Markdown memory injected as a system-prompt prelude. |
| `output_style` | `terse \| narrative \| json` | `narrative` | Affects free-form replies; structured JSON outputs unaffected. |
| `mcp_servers` | list of `{name, command, env?, cwd?}` | `[]` | Discovered tools register as `mcp__<server>__<tool>`. |
| `models[<role>].temperature / top_p / seed / repetition_penalty` | numeric | model-specific | Per-role determinism; reviewer defaults to `temperature: 0.0`. `--seed` overrides every role for the run. |
| `git.worktrees` | bool | `false` | Speculative coder retries inside isolated `git worktree` instances. |
| `hooks.path` | path | unset | Discovers a Python file with `pre_tool` / `post_tool` / `pre_phase` / `post_phase`. |
| `otel.enabled` / `otel.endpoint` / `otel.service_name` | bool / str / str | `false` / unset / `lao` | Optional OpenTelemetry HTTP OTLP exporter. `LAO_OTEL_ENDPOINT` env var also enables it. |

### New CLI surfaces

```
lao doctor                # grouped LM Studio / models / disk / schema diagnostics
lao mcp-server            # expose LAO tools to other agents over stdio
lao skills [list|show <name>]
lao memory  [show|edit "fact"|forget "substring"]
lao run --permission-mode {auto|confirm|plan_only|bypass}
lao run --seed <int>
lao run --output-style {terse|narrative|json}
```
