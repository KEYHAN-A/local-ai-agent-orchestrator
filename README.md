# LAO — Local AI Agent Orchestrator

A pilot-mode AI agent orchestration system that coordinates planner → coder → verifier → reviewer workflows over LM Studio / OpenAI-compatible APIs. Features structured tools, permissions, skills, hierarchical memory, optional MCP support, SQLite state, and optional per-plan Git integration.

## Overview

LAO is a local-first AI agent orchestrator designed for complex software development tasks. It breaks down work into phases, assigns specialized models to each phase, and validates output through automated checks.

**Key capabilities**:
- **Pilot Mode** — phased orchestration with quality gates
- **Specialized Models** — different LLMs for planning, coding, reviewing, and analysis
- **Hierarchical Memory** — project-wide and user-level memory injection
- **Tool Permissions** — configurable allow/deny lists for agent actions
- **Skills System** — named, reusable workflow definitions
- **Conversation Compaction** — intelligent context summarization
- **Mechanical Verification** — file existence, AST/JSON parse checks between coder and reviewer
- **Git Integration** — per-plan commit trails (optional)
- **MCP Support** — external model context protocol servers (optional)

## Features

- Multi-model orchestration (planner, coder, reviewer, analyst, embedder)
- LM Studio and OpenAI-compatible API backends
- Structured tool use with permission system
- Hierarchical memory (project + user level)
- Conversation compaction to manage context windows
- Quality gates with configurable confidence thresholds
- Validation profiles (build, lint, security)
- Per-plan Git integration for audit trails
- OpenTelemetry tracing support
- CLI interface with rich output formatting

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| LLM API | OpenAI Python SDK (compatible with LM Studio) |
| CLI | prompt_toolkit + questionary |
| Output | Rich (terminal formatting) |
| State | SQLite (via pyproject config) |
| Config | PyYAML |
| Tokenization | tiktoken |
| File Watching | watchdog |
| Testing | pytest |

## Project Structure

```
local-ai-agent-orchestrator/
├── main.py                    # Entry point
├── pyproject.toml             # Package config, dependencies, entry points
├── requirements.txt           # pip dependencies
├── factory.example.yaml       # Model & orchestration config template
├── .env.example               # Environment variables
├── CHANGELOG.md               # Version history
├── LICENSE                    # GPL-3.0
├── docs/                      # Documentation
├── src/                       # Source code
│   └── local_ai_agent_orchestrator/
│       ├── cli.py             # CLI entry point
│       └── (orchestration modules)
├── workspace/                 # Active workspace state
├── plans/                     # Generated plans
├── tests/                     # Pytest test suite
├── dist/                      # Distribution artifacts
├── build/                     # Build output
├── scripts/                   # Utility scripts
└── .lao/                      # Per-project state (state.db, hooks, etc.)
```

## Installation

### From Source (Recommended)

```bash
cd ~/projects/local-ai-agent-orchestrator

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .

# Or from requirements.txt
pip install -r requirements.txt
```

### CLI Entry Points

```bash
lao                  # Main CLI
local-ai-agent-orchestrator   # Alias
```

## Configuration

### Model Configuration (`factory.yaml`)

Copy the example and customize:

```bash
cp factory.example.yaml factory.yaml
```

Key sections:

- **`lm_studio_base_url`** — LM Studio server URL (default: `http://127.0.0.1:1234`)
- **`models`** — Define planner, coder, reviewer, embedder, analyst models
- **`orchestration`** — Phase gating, retry logic, quality gates
- **`permissions`** — Tool permission mode (`auto`, `confirm`, `plan_only`, `bypass`)
- **`memory_enabled`** — Hierarchical memory injection
- **`git.enabled`** — Per-plan Git commit trails
- **`verifier_enabled`** — Mechanical verification between coder and reviewer
- **`mcp_servers`** — External MCP server definitions (optional)

### Environment Variables

```bash
cp .env.example .env
```

## Usage

### Run LAO

```bash
# With default config
lao run

# With custom config
lao run --config factory.yaml

# Without analyst phase
lao run --no-analyst

# With different output style
lao run --output-style narrative   # terse | narrative | json
```

### Model Configuration Example

```yaml
models:
  planner:
    key: qwen_qwen3.5-35b-a3b
    context_length: 32768
    max_completion: 16384
    supports_tools: true
  coder:
    key: qwen/qwen3-coder-30b
    context_length: 16384
    max_completion: 4096
    supports_tools: true
  reviewer:
    key: deepseek-r1-distill-qwen-32b
    context_length: 8192
    max_completion: 2048
    supports_tools: false
  analyst:
    key: qwen2.5-7b-instruct
    context_length: 65536
    max_completion: 8192
  embedder:
    key: text-embedding-nomic-embed-text-v1.5
    context_length: 2048
```

## Development

```bash
# Run tests
pytest

# Type checking
mypy src/

# Linting
ruff check src/

# Format
ruff format src/

# Build distribution
python -m build

# Install from build
pip install dist/local_ai_agent_orchestrator-*.whl
```

## Orchestration Pipeline

```
User Request
    │
    ▼
┌─────────┐     ┌─────────┐     ┌──────────┐     ┌──────────┐
│ Analyst  │ →   │ Planner  │ →   │   Coder  │ →   │ Verifier │
│(Read-    │     │(Architect│     │(Code Gen │     │(Mech-   │
│ only)    │     │ / Plan)  │     │ er)     │     │ anical)  │
└─────────┘     └─────────┘     └──────────┘     └──────────┘
                                                        │
                                                        ▼
                                                 ┌──────────┐
                                                 │ Reviewer │
                                                 │(Quality  │
                                                  │ Gate)    │
                                                 └──────────┘
```

## Quality Gates

- **Standard Mode**: Blocks on critical/major severities, min confidence 0.6
- **Swift/iOS Mode**: Xcode build + SwiftLint validation
- **Security Mode**: Semgrep + Bandit analysis
- Configurable per validation profile

## License

GPL-3.0 — see [LICENSE](LICENSE) for details.

## Links

- **Homepage**: https://lao.keyhan.info
- **Repository**: https://github.com/KEYHAN-A/local-ai-agent-orchestrator
- **Documentation**: https://lao.keyhan.info
