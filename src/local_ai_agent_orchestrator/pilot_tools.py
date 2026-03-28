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
    pick_pilot_tools_workspace,
)
from local_ai_agent_orchestrator.validators import infer_plan_languages, infer_validation_commands

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
    raw = (title or "").strip()
    if raw.lower().endswith(".md"):
        raw = raw[:-3].strip()
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in raw).strip()
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
            ws = q.workspace_for_plan(p["id"])
            lines.append(
                f"  id={p['id']}  file={p['filename']} [{p['status']}] "
                f"workspace={ws}"
                f" — {len(completed)}/{len(plan_tasks)} done"
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
    resolved: str | None = None
    if plan_id is not None and str(plan_id).strip():
        ref = str(plan_id).strip()
        resolved = _QUEUE_REF.resolve_plan_ref(ref)
        if not resolved:
            return (
                f"ERROR: No plan matches {ref!r}. "
                "Use pipeline_status for each plan's id= and file= values."
            )
    count = _QUEUE_REF.reset_failed_tasks(plan_id=resolved)
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


def project_status(name_or_path: str = "") -> str:
    """Return status of a registered project or list all known projects."""
    from local_ai_agent_orchestrator.project_registry import ProjectRegistry

    reg = ProjectRegistry()
    if name_or_path:
        entry = reg.get(name_or_path)
        if not entry:
            return f"ERROR: Project '{name_or_path}' not found in registry."
        entry = reg.refresh(entry)
        return (
            f"Project: {entry.name}\n"
            f"  Path: {entry.path}\n"
            f"  Config: {'yes' if entry.has_config else 'no'}\n"
            f"  Plans: {entry.plans_count}\n"
            f"  Pending tasks: {entry.pending_tasks}\n"
            f"  Failed tasks: {entry.failed_tasks}\n"
            f"  Last used: {entry.last_used}"
        )
    entries = reg.list_all()
    if not entries:
        return "No projects registered. The user can run /project scan to discover LAO projects."
    lines = [f"Registered projects ({len(entries)}):"]
    for e in entries:
        e = reg.refresh(e)
        status = []
        if e.pending_tasks:
            status.append(f"{e.pending_tasks} pending")
        if e.failed_tasks:
            status.append(f"{e.failed_tasks} failed")
        tag = f" ({', '.join(status)})" if status else ""
        lines.append(f"  {e.name}{tag}  {e.path}")
    return "\n".join(lines)


def gate_summary(plan_ref: str | None = None) -> str:
    """Summarize validation profile, explicit commands, and manifest-inferred build/lint hints."""
    s = get_settings()
    ws: Path
    if _QUEUE_REF is not None:
        if plan_ref and str(plan_ref).strip():
            pid = _QUEUE_REF.resolve_plan_ref(str(plan_ref).strip())
            if not pid:
                return (
                    f"ERROR: No plan matches {plan_ref!r}. "
                    "Use pipeline_status for id= or file= values."
                )
            ws = _QUEUE_REF.workspace_for_plan(pid)
        else:
            ws = pick_pilot_tools_workspace(_QUEUE_REF)
    else:
        ws = s.workspace_root.resolve()
    if not ws.is_dir():
        return f"ERROR: Workspace path is not a directory: {ws}"

    langs = infer_plan_languages(ws)
    ib, il = infer_validation_commands(ws, langs)
    profile_name = s.validation_profile
    profile = s.validation_profiles.get(
        profile_name, s.validation_profiles.get("default", {})
    )
    lines = [
        "## Validation gates",
        f"- Workspace: `{ws}`",
        f"- Active profile: `{profile_name}`",
        f"- Manifest inference (orchestration.infer_validation_commands): {s.infer_validation_commands}",
    ]
    cmds = profile.get("commands") or []
    if cmds:
        lines.append("- Profile commands:")
        for row in cmds:
            if isinstance(row, dict) and row.get("command"):
                lines.append(f"  - [{row.get('kind', 'cmd')}] {row['command']}")
    else:
        lines.append("- Profile commands: (none)")
    if s.validation_build_cmd:
        lines.append(f"- Explicit **validation_build_cmd**: `{s.validation_build_cmd}`")
    if s.validation_lint_cmd:
        lines.append(f"- Explicit **validation_lint_cmd**: `{s.validation_lint_cmd}`")
    lines.append("### Inferred when build/lint slots are free")
    lines.append(f"- Suggested build: `{ib or '—'}`")
    lines.append(f"- Suggested lint: `{il or '—'}`")
    lang_display = ", ".join(sorted(langs)) if langs else "(none from plan / extensions)"
    lines.append(f"- Plan / extension language hints: {lang_display}")
    return "\n".join(lines)


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
                        "description": (
                            "Optional: scope retry to one plan. Use internal id or filename "
                            "(e.g. MyPlan.md) from pipeline_status."
                        ),
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
    {
        "type": "function",
        "function": {
            "name": "project_status",
            "description": "Get status of a registered LAO project or list all known projects",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_or_path": {
                        "type": "string",
                        "description": "Project name or path (omit to list all)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gate_summary",
            "description": (
                "Show validation gate configuration: profile commands, explicit build/lint YAML, "
                "and conservative commands inferred from package manifests in the workspace"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_ref": {
                        "type": "string",
                        "description": (
                            "Optional plan id, filename, or stem to scope the workspace; "
                            "omit to use the pilot tool workspace pick"
                        ),
                    },
                },
                "required": [],
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
    "project_status": project_status,
    "gate_summary": lambda plan_ref=None, **kw: gate_summary(plan_ref),
}
