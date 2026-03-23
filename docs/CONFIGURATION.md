# Configuration

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
| `paths.workspace` | Optional fallback when no per-plan context is active (default: `.lao/_misc`) |
| `memory_gate.*` | `release_fraction`, `swap_growth_limit_mb`, `settle_timeout_s`, `poll_interval_s` |
| `orchestration.*` | Timeouts, retries, `max_task_attempts`, `plan_watch_interval_s` |
| `git.enabled` | When **true** (default), run Git commits in each **per-plan project folder** (`./<plan-stem>/`). Requires **`git`** on `PATH` and committer identity. Override with CLI **`--no-git`**. |
| `git.plan_file_name` | Snapshot filename for the plan markdown (default **`LAO_PLAN.md`**) |
| `git.commit_trailers` | When **true**, add **`LAO-Plan-ID`** / **`LAO-Task-ID`** lines to the commit body (second `-m` paragraph) |
| `models.<role>` | `key`, `context_length`, `max_completion`, `supports_tools`, `size_bytes`, `description` |

Roles: `planner`, `coder`, `reviewer`, `embedder`.

**Planner (`models.planner`):** Large markdown plans need a high **`context_length`** so the full plan fits in the prompt. The architect emits a **JSON array of micro-tasks**, which can be long — set **`max_completion`** high as well (defaults in `factory.example.yaml` use `32768` / `16384`). If you see truncated JSON or `finish_reason=length` errors, increase both values in **`factory.yaml`** and reload the model in LM Studio with the same context size.

**Reviewer (`models.reviewer`):** Reasoning models (e.g. DeepSeek-R1 distill) may emit *think*-tagged chain-of-thought before `APPROVED` or `REJECTED: …`. As of **v1.1.0**, the orchestrator strips those blocks and scans line-by-line for the verdict, so you only need a valid **`key`** and appropriate **`context_length`** / **`max_completion`** in YAML.

### Per-plan project folders (v1.2.0+)

- Code for `plans/MyFeature.md` is written under **`<factory.yaml directory>/MyFeature/`** (same stem as the plan filename, next to `plans/`).
- **`README.md`** in `plans/` is **ignored** when scanning for new plans (so it is not decomposed as a project plan).
- **`lao init`** creates **`.lao/`**, **`plans/`**, an optional root **`README.md`** (workspace guide), and **`factory.example.yaml`**.
- SQLite and WAL files belong under **`.lao/`** when using the example `paths.database` — keeps stray `NANO*` / WAL files out of the repo root.

### Discovering model `key` values

- LM Studio CLI: `lms ls`
- HTTP: `GET http://127.0.0.1:1234/v1/models`

`size_bytes` should match on-disk size from `lms ls` (used for memory-gate bookkeeping).

## Environment variables

See [.env.example](../.env.example). `LAO_CONFIG` can point to an absolute path to your yaml.

## CLI overrides

CLI flags override YAML after merge. Example:

```bash
lao --lm-studio-url http://192.168.1.10:1234 --ram-gb 64 \
  --reviewer-model my-reviewer-mlx run
```
