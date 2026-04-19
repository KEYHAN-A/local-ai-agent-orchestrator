# SPDX-License-Identifier: GPL-3.0-or-later
"""
Workspace plumbing for the tools package.

Holds the active workspace context-vars, project access escape hatch, plan-mode
flag, and the path-resolution helper used by every file/shell tool. Kept apart
from the individual tool implementations so it can be imported safely from
phases / pilot / permissions without circular imports.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from local_ai_agent_orchestrator.settings import get_settings

if TYPE_CHECKING:
    from local_ai_agent_orchestrator.state import TaskQueue


_ACTIVE_WORKSPACE: ContextVar[Optional[Path]] = ContextVar(
    "lao_active_workspace", default=None
)
_ALLOWED_PROJECT: ContextVar[Optional[Path]] = ContextVar(
    "lao_allowed_project", default=None
)
_PLAN_MODE: ContextVar[bool] = ContextVar("lao_plan_mode", default=False)


# ── Workspace context ────────────────────────────────────────────────


def _workspace_root() -> Path:
    w = _ACTIVE_WORKSPACE.get()
    if w is not None:
        return w.resolve()
    return get_settings().workspace_root.resolve()


def tools_workspace_root() -> Path:
    """Root directory used by file_read / list_dir / shell_exec."""
    return _workspace_root().resolve()


@contextmanager
def use_plan_workspace(queue: "TaskQueue", plan_id: str) -> Iterator[Path]:
    """Set the active workspace to ``<config_dir>/<plan-stem>/`` for the block."""
    path = queue.workspace_for_plan(plan_id)
    token = _ACTIVE_WORKSPACE.set(path)
    try:
        yield path
    finally:
        _ACTIVE_WORKSPACE.reset(token)


def push_active_workspace(path: Path) -> object:
    return _ACTIVE_WORKSPACE.set(path.resolve())


def reset_active_workspace(token: object) -> None:
    _ACTIVE_WORKSPACE.reset(token)


def pick_pilot_tools_workspace(queue: "TaskQueue") -> Path:
    """Newest plan folder with actionable work, else the LAO config directory."""
    s = get_settings()
    plans = queue.get_plans()
    actionable = {"failed", "pending", "rework", "coded", "review", "coding"}
    for p in reversed(plans):
        for t in queue.get_plan_tasks(p["id"]):
            if t.status in actionable:
                return queue.workspace_for_plan(p["id"])
    return s.config_dir.resolve()


# ── Project access escape hatch ──────────────────────────────────────


@contextmanager
def allow_project_access(project_path: Path) -> Iterator[None]:
    """Temporarily allow tool access to *project_path* in addition to the workspace."""
    token = _ALLOWED_PROJECT.set(project_path.resolve())
    try:
        yield
    finally:
        _ALLOWED_PROJECT.reset(token)


# ── Plan-mode flag (read by permission system) ───────────────────────


@contextmanager
def plan_mode(active: bool = True) -> Iterator[None]:
    token = _PLAN_MODE.set(bool(active))
    try:
        yield
    finally:
        _PLAN_MODE.reset(token)


def is_plan_mode() -> bool:
    return bool(_PLAN_MODE.get())


def set_plan_mode(active: bool) -> object:
    return _PLAN_MODE.set(bool(active))


def reset_plan_mode(token: object) -> None:
    _PLAN_MODE.reset(token)


# ── Path resolution ──────────────────────────────────────────────────


def resolve_path(path: str | None) -> Optional[Path]:
    """Resolve *path* relative to the workspace, refusing escapes.

    Returns ``None`` if the path resolves outside both the workspace and the
    optionally-allowed project directory.
    """
    root = _workspace_root().resolve()
    if path is None:
        return root
    p = Path(path)
    resolved = p.resolve() if p.is_absolute() else (root / p).resolve()
    roots = [root]
    allowed = _ALLOWED_PROJECT.get()
    if allowed is not None:
        roots.append(allowed)
    for candidate in roots:
        try:
            resolved.relative_to(candidate)
            return resolved
        except ValueError:
            continue
    return None


def human_size(nbytes: int | float) -> str:
    n = float(nbytes)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"
