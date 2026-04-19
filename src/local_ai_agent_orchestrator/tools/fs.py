# SPDX-License-Identifier: GPL-3.0-or-later
"""Filesystem tools: read / write / patch / list directory."""

from __future__ import annotations

import logging
from pathlib import Path

from local_ai_agent_orchestrator.tools.base import (
    PermissionDecision,
    Tool,
    param,
    parameters_schema,
    register,
)
from local_ai_agent_orchestrator.tools.meta import (
    human_size,
    is_plan_mode,
    resolve_path,
)

log = logging.getLogger(__name__)


# ── Implementations ──────────────────────────────────────────────────


def file_read(path: str, max_lines: int = 500) -> str:
    """Read a file from the workspace."""
    full = resolve_path(path)
    if not full:
        return f"ERROR: Path '{path}' is outside the workspace."
    if not full.exists():
        return f"ERROR: File not found: {path}"
    if not full.is_file():
        return f"ERROR: Not a file: {path}"
    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n\n... truncated ({len(lines)} total lines)"
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


def file_write(path: str, content: str) -> str:
    full = resolve_path(path)
    if not full:
        return f"ERROR: Path '{path}' is outside the workspace."
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        log.info(f"[Tools] Wrote {len(content)} chars to {path}")
        return f"OK: Written to {path}"
    except Exception as e:
        return f"ERROR: {e}"


def file_patch(path: str, old: str, new: str) -> str:
    full = resolve_path(path)
    if not full:
        return f"ERROR: Path '{path}' is outside the workspace."
    if not full.exists():
        return f"ERROR: File not found: {path}"
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
        if old not in content:
            return f"ERROR: The old string was not found in {path}"
        content = content.replace(old, new, 1)
        full.write_text(content, encoding="utf-8")
        return f"OK: Patched {path}"
    except Exception as e:
        return f"ERROR: {e}"


def list_dir(path: str = ".", max_depth: int = 3) -> str:
    full = resolve_path(path)
    if not full:
        return f"ERROR: Path '{path}' is outside the workspace."
    if not full.exists():
        return f"ERROR: Directory not found: {path}"
    if not full.is_dir():
        return f"ERROR: Not a directory: {path}"
    lines: list[str] = []
    _walk_tree(full, full, lines, max_depth, 0)
    if not lines:
        return "(empty directory)"
    return "\n".join(lines)


def _walk_tree(root: Path, current: Path, lines: list[str], max_depth: int, depth: int) -> None:
    if depth > max_depth:
        return
    try:
        entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name))
    except PermissionError:
        return
    for entry in entries:
        if entry.name.startswith("."):
            continue
        rel = entry.relative_to(root)
        prefix = "  " * depth
        if entry.is_dir():
            lines.append(f"{prefix}{rel}/")
            _walk_tree(root, entry, lines, max_depth, depth + 1)
        else:
            size = entry.stat().st_size
            lines.append(f"{prefix}{rel}  ({human_size(size)})")


# ── Permission gates ─────────────────────────────────────────────────


def _deny_in_plan_mode(_args: dict) -> PermissionDecision | None:
    if is_plan_mode():
        return PermissionDecision.deny(
            reason="plan_mode_active",
            prompt="Mutating tools are blocked while plan mode is active. "
            "Call exit_plan_mode after the user approves the plan.",
        )
    return None


# ── Tool definitions ─────────────────────────────────────────────────

FILE_READ = register(
    Tool(
        name="file_read",
        description="Read a file from the project workspace",
        parameters=parameters_schema(
            {
                "path": param("string", "Relative path from workspace root"),
                "max_lines": param("integer", "Max lines to return (default 500)"),
            },
            required=["path"],
        ),
        call=file_read,
        is_read_only=True,
        is_concurrency_safe=True,
        plan_mode_safe=True,
        prompt_contribution=(
            "file_read(path) returns up to max_lines of the file. "
            "Always read target files BEFORE editing them."
        ),
    )
)

FILE_WRITE = register(
    Tool(
        name="file_write",
        description="Write content to a file (creates dirs as needed)",
        parameters=parameters_schema(
            {
                "path": param("string", "Relative path from workspace root"),
                "content": param("string", "Full file content to write"),
            },
            required=["path", "content"],
        ),
        call=file_write,
        is_read_only=False,
        is_concurrency_safe=False,
        check_permissions=_deny_in_plan_mode,
        prompt_contribution=(
            "file_write OVERWRITES the file. Read existing content first when in doubt."
        ),
    )
)

FILE_PATCH = register(
    Tool(
        name="file_patch",
        description="Replace a specific string in an existing file",
        parameters=parameters_schema(
            {
                "path": param("string", "Relative path from workspace root"),
                "old": param("string", "Exact string to find"),
                "new": param("string", "Replacement string"),
            },
            required=["path", "old", "new"],
        ),
        call=file_patch,
        is_read_only=False,
        is_concurrency_safe=False,
        check_permissions=_deny_in_plan_mode,
        prompt_contribution=(
            "file_patch performs a single string replacement; the old string must be unique."
        ),
    )
)

LIST_DIR = register(
    Tool(
        name="list_dir",
        description="List directory contents with tree structure",
        parameters=parameters_schema(
            {"path": param("string", "Relative path (default: workspace root)")},
        ),
        call=list_dir,
        is_read_only=True,
        is_concurrency_safe=True,
        plan_mode_safe=True,
    )
)
