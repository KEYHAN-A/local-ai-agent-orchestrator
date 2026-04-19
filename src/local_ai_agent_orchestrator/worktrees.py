# SPDX-License-Identifier: GPL-3.0-or-later
"""
Speculative coder retries inside ``git worktree add``.

Gated by ``factory.yaml: git.worktrees: true``. When enabled the runner can
attempt a retry inside a disposable worktree at
``<plan-workspace>/.lao/_worktrees/task-<id>-attempt-<n>/`` and merge the
worktree branch back into the per-plan branch on approval.

This module is purposely small and independent: callers that don't enable
worktrees in settings simply never invoke it.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from local_ai_agent_orchestrator.settings import get_settings

log = logging.getLogger(__name__)


def worktrees_enabled() -> bool:
    try:
        return bool(get_settings().git.worktrees)
    except RuntimeError:
        return False


def _git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        check=check,
    )


def _is_git_repo(workspace: Path) -> bool:
    try:
        r = _git(workspace, "rev-parse", "--is-inside-work-tree", check=False)
        return r.returncode == 0 and "true" in (r.stdout or "").lower()
    except FileNotFoundError:
        return False


def attempt_branch_name(task_id: int, attempt: int) -> str:
    return f"lao/task-{task_id}/attempt-{attempt}"


def worktree_dir(workspace: Path, task_id: int, attempt: int) -> Path:
    return (workspace / ".lao" / "_worktrees" / f"task-{task_id}-attempt-{attempt}").resolve()


def create(workspace: Path, task_id: int, attempt: int) -> Optional[Path]:
    """Create a worktree for the given attempt; returns the path or None on failure."""
    if not worktrees_enabled() or not _is_git_repo(workspace):
        return None
    branch = attempt_branch_name(task_id, attempt)
    target = worktree_dir(workspace, task_id, attempt)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        _git(workspace, "worktree", "add", "-b", branch, str(target))
    except subprocess.CalledProcessError as e:
        log.warning(f"[Worktrees] add failed: {e.stderr.strip() or e}")
        return None
    log.info(f"[Worktrees] created {target} on branch {branch}")
    return target


def merge(workspace: Path, task_id: int, attempt: int) -> bool:
    """Merge the worktree branch back into the current branch (fast-forward)."""
    if not _is_git_repo(workspace):
        return False
    branch = attempt_branch_name(task_id, attempt)
    try:
        _git(workspace, "merge", "--ff", branch)
    except subprocess.CalledProcessError as e:
        log.warning(f"[Worktrees] merge failed: {e.stderr.strip() or e}")
        return False
    return True


def drop(workspace: Path, task_id: int, attempt: int) -> None:
    """Remove the worktree and delete its branch."""
    target = worktree_dir(workspace, task_id, attempt)
    branch = attempt_branch_name(task_id, attempt)
    try:
        _git(workspace, "worktree", "remove", "--force", str(target), check=False)
    except Exception:
        pass
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        _git(workspace, "branch", "-D", branch, check=False)
    except Exception:
        pass


@contextmanager
def attempt_worktree(workspace: Path, task_id: int, attempt: int) -> Iterator[Optional[Path]]:
    """Yield a worktree path for the attempt; None if disabled / not a git repo."""
    target = create(workspace, task_id, attempt)
    try:
        yield target
    finally:
        if target is not None:
            # Caller decides whether to merge before exiting; we still drop the
            # worktree directory but keep the branch for inspection if the
            # merge already happened (branch deletion is idempotent).
            drop(workspace, task_id, attempt)
