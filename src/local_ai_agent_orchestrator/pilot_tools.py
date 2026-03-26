# SPDX-License-Identifier: GPL-3.0-or-later
"""
Pilot-mode tool schemas and dispatch.

Extends the workspace tools from tools.py with pilot-specific operations
(plan creation, pipeline introspection, retry/resume).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.tools import (
    TOOL_DISPATCH,
    TOOL_SCHEMAS,
    file_read,
    file_write,
    file_patch,
    list_dir,
    shell_exec,
    find_relevant_files,
)

if TYPE_CHECKING:
    from local_ai_agent_orchestrator.state import TaskQueue

log = logging.getLogger(__name__)

_QUEUE_REF: Optional["TaskQueue"] = None
_RESUME_REQUESTED = False


def bind_queue(queue: Optional["TaskQueue"]) -> None:
    global _QUEUE_REF
    _QUEUE_REF = queue


def is_resume_requested() -> bool:
    return _RESUME_REQUESTED


def reset_resume_flag() -> None:
    global _RESUME_REQUESTED
    _RESUME_REQUESTED = False


# ── Pilot-specific tools ─────────────────────────────────────────────


def create_plan(title: str, content: str) -> str:
    """Write a new plan .md file into the plans directory for pipeline processing."""
    s = get_settings()
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in title).strip()
    if not safe_title:
        return "ERROR: Title must contain at least one alphanumeric character."
    filename = safe_title.replace(" ", "_") + ".md"
    plan_path = s.plans_dir / filename
    if plan_path.exists():
        return f"ERROR: Plan already exists: {filename}. Choose a different title."
    try:
        s.plans_dir.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(content, encoding="utf-8")
        log.info(f"[Pilot] Created plan: {plan_path}")
        return f"OK: Created plan {filename} in {s.plans_dir}. Use /resume to start the pipeline."
    except Exception as e:
        return f"ERROR: {e}"


def pipeline_status() -> str:
    """Return a summary of the current pipeline state."""
    if _QUEUE_REF is None:
        return "ERROR: Pipeline queue not available."
    q = _QUEUE_REF
    stats = q.get_stats()
    plans = q.get_plans()
    tokens = q.get_total_tokens()

    lines = ["## Pipeline Status"]

    if not stats:
        lines.append("No tasks in queue.")
    else:
        total = sum(stats.values())
        lines.append(f"\nTask Queue ({total} total):")
        for status, count in sorted(stats.items()):
            lines.append(f"  {status}: {count}")

    if plans:
        lines.append(f"\nPlans ({len(plans)}):")
        for p in plans:
            plan_tasks = q.get_plan_tasks(p["id"])
            failed = [t for t in plan_tasks if t.status == "failed"]
            completed = [t for t in plan_tasks if t.status == "completed"]
            lines.append(
                f"  {p['filename']} [{p['status']}] "
                f"— {len(completed)}/{len(plan_tasks)} done"
                + (f", {len(failed)} failed" if failed else "")
            )
            if failed:
                for t in failed[:5]:
                    reason = (t.escalation_reason or "unknown")
                    lines.append(f"    FAILED #{t.id}: {t.title} ({reason})")

    total_tok = tokens["prompt_tokens"] + tokens["completion_tokens"]
    lines.append(f"\nTokens used: {total_tok:,}")
    return "\n".join(lines)


def retry_failed(plan_id: str | None = None) -> str:
    """Reset failed tasks to pending so the pipeline can retry them."""
    if _QUEUE_REF is None:
        return "ERROR: Pipeline queue not available."
    count = _QUEUE_REF.reset_failed_tasks(plan_id=plan_id)
    if count == 0:
        return "No failed tasks to retry."
    return f"OK: Reset {count} failed task(s) to pending. Use /resume to start the pipeline."


def resume_pipeline() -> str:
    """Signal the pilot loop to exit and return control to the autopilot pipeline."""
    global _RESUME_REQUESTED
    _RESUME_REQUESTED = True
    return "OK: Pipeline will resume after this response."


def codebase_search(query: str, top_k: int = 5) -> str:
    """Search the codebase using semantic similarity or keyword matching."""
    try:
        results = find_relevant_files(query, top_k=top_k)
        if not results:
            return "No matching files found."
        lines = [f"Found {len(results)} relevant file(s):"]
        for path, score in results:
            lines.append(f"  {path} (score: {score:.3f})")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: Search failed: {e}"


# ── Combined schemas and dispatch ────────────────────────────────────

PILOT_TOOL_SCHEMAS = list(TOOL_SCHEMAS) + [
    {
        "type": "function",
        "function": {
            "name": "create_plan",
            "description": "Create a new plan .md file for the LAO pipeline to process",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Plan title (used as filename)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full markdown content of the plan",
                    },
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pipeline_status",
            "description": "Get current pipeline status: task queue stats, plan progress, failures",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retry_failed",
            "description": "Reset failed tasks to pending so the pipeline can retry them",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {
                        "type": "string",
                        "description": "Optional plan ID to scope retry (omit for all)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_pipeline",
            "description": "Exit pilot mode and resume the autopilot pipeline",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "codebase_search",
            "description": "Search the codebase for files relevant to a query",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Max results to return (default 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

PILOT_TOOL_DISPATCH = {
    **TOOL_DISPATCH,
    "create_plan": create_plan,
    "pipeline_status": lambda **kw: pipeline_status(),
    "retry_failed": retry_failed,
    "resume_pipeline": lambda **kw: resume_pipeline(),
    "codebase_search": codebase_search,
}
