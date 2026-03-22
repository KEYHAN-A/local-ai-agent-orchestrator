# Local AI Agent Orchestrator

**License:** [GPL-3.0-only](LICENSE)

A lightweight, framework-free **multi-agent coding orchestrator** for **local LLMs** served by [LM Studio](https://lmstudio.ai/) (OpenAI-compatible API). It runs a **planner → coder → reviewer** pipeline with **SQLite-backed** task queues, **explicit model load/unload**, and a **macOS memory gate** to reduce swap thrashing when swapping 20GB+ models on unified memory.

- **Planner:** decomposes a master plan into file-level micro-tasks (JSON).
- **Coder:** implements tasks with tool use (`file_read`, `file_write`, `shell_exec`, …).
- **Reviewer:** validates output (APPROVED / REJECTED with feedback).
- **Embedder:** optional semantic file retrieval before coding (Nomic via LM Studio).

## Why not CrewAI / LangChain here?

This project uses the **OpenAI Python SDK** directly against LM Studio to avoid multi-agent framework token overhead (ReAct scaffolding) on small local context windows.

## Requirements

- Python **3.10+**
- LM Studio with local server enabled
- Models you configure in `factory.yaml` (see [docs/CONFIGURATION.md](docs/CONFIGURATION.md))
- **Apple Silicon tip:** disable overly strict LM Studio **Model Loading Guardrails** if large models fail to load (Developer → Server Settings).

## Install

```bash
cd local-ai-agent-orchestrator
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Or run without install (adds `src/` automatically):

```bash
python main.py health
```

## Quick start

```bash
# Generate example config
lao init
cp factory.example.yaml factory.yaml
# Edit factory.yaml: set lm_studio_base_url, total_ram_gb, and model keys from `lms ls`

lao health
lao run
# In another terminal: add plans/*.md or use:
lao run --plan plans/my_project.md --single-run
```

### CLI (`lao`)

| Command | Description |
|--------|-------------|
| `lao run` | Watch `plans/`, process queue (default if no subcommand) |
| `lao run --plan FILE` | Ingest one plan and process |
| `lao run --single-run` | One pass then exit |
| `lao status` | SQLite queue + token stats |
| `lao health` | LM Studio reachability + model keys |
| `lao reset-failed` | Move `failed` tasks back to `pending` |
| `lao init` | Write `factory.example.yaml`, ensure dirs |

### Global flags

| Flag | Purpose |
|------|---------|
| `--config PATH` | `factory.yaml` (default: `./factory.yaml` if present) |
| `--lm-studio-url URL` | Override base URL |
| `--ram-gb N` | Total RAM (logged; future tuning) |
| `--workspace`, `--plans-dir`, `--db` | Paths |
| `--planner-model`, `--coder-model`, `--reviewer-model`, `--embedder-model` | Override keys without editing YAML |

Environment: `LM_STUDIO_BASE_URL`, `OPENAI_API_KEY`, `LAO_CONFIG` (path to yaml), `TOTAL_RAM_GB`, `WORKSPACE_ROOT`, `PLANS_DIR`, `DB_PATH`. See [.env.example](.env.example).

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```mermaid
flowchart LR
  Plan[Markdown plan] --> Arch[Planner LLM]
  Arch --> Q[SQLite queue]
  Q --> Code[Coder LLM]
  Code --> Rev[Reviewer LLM]
  Rev --> Q
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)
- [site/index.html](site/index.html) — open in a browser for a simple landing page

## Publish to GitHub (you run these)

1. Create a **public** repository, e.g. `local-ai-agent-orchestrator`.
2. Update `pyproject.toml` `[project.urls] Homepage` with your repo URL.
3. Commit and push:

```bash
git remote add origin git@github.com:YOUR_USER/local-ai-agent-orchestrator.git
git branch -M main
git add -A && git commit -m "chore: initial open source release"
git push -u origin main
```

Replace `YOUR_USER` and use HTTPS if you prefer.

## Disclaimer

This software can execute shell commands and write files as configured. Run in a trusted workspace. GPL-3.0 applies to this project; third-party libraries have their own licenses.
