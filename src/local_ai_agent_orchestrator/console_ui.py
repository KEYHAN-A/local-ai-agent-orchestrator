# SPDX-License-Identifier: GPL-3.0-or-later
"""
Console UI utilities for LAO CLI.

The full-screen RunDashboard has been replaced by the unified scrolling UI in
unified_ui.py.  This module keeps workspace README generation and the
apply_runner_context bridge (re-exported from unified_ui for backward compat).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from local_ai_agent_orchestrator.branding import AUTHOR, UPSTREAM_REPO

# Re-export so existing call-sites (if any) still resolve.
from local_ai_agent_orchestrator.unified_ui import apply_runner_context  # noqa: F401


def workspace_readme_body() -> str:
    return f"""# LAO workspace

This directory is a **Local AI Agent Orchestrator (LAO)** factory: a multi-agent coding pipeline that drives **local LLMs** (for example via [LM Studio](https://lmstudio.ai/))—no cloud API required for the runtime.

## Layout

| Path | Purpose |
|------|---------|
| `plans/` | Markdown plans (`MyFeature.md`). `plans/README.md` is never treated as a plan. |
| `{{plan-stem}}/` | Code for `plans/{{plan-stem}}.md` is written here, next to `plans/`. |
| `.lao/` | Internal state: SQLite DB, optional caches, fallback `.lao/_misc/`. |
| `factory.yaml` | Your config (model keys, paths, orchestration). Copy from `factory.example.yaml`. |

## Quick start

1. Copy `factory.example.yaml` to `factory.yaml` and set model IDs to match LM Studio.
2. Start LM Studio and enable the local server.
3. Run: `lao run` (or `lao --plan plans/YourPlan.md --single-run run`).

Upstream: {UPSTREAM_REPO}

Developer: {AUTHOR}
"""


def write_workspace_readme(root: Path) -> bool:
    dest = Path(root) / "README.md"
    if dest.exists():
        return False
    dest.write_text(workspace_readme_body(), encoding="utf-8")
    return True
