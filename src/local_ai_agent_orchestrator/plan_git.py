# SPDX-License-Identifier: GPL-3.0-or-later
"""
Per-plan Git: plan snapshot, task manifest, phase commits via system git.

Uses ``git -C <workspace>`` subprocess calls; no extra Python dependencies.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from local_ai_agent_orchestrator.settings import get_settings

if TYPE_CHECKING:
    from local_ai_agent_orchestrator.state import TaskQueue

log = logging.getLogger(__name__)

_MINIMAL_GITIGNORE = """.DS_Store
__pycache__/
.venv/
.env
*.pyc
"""

_identity_warned = False

TASKS_ARTIFACT = "LAO_TASKS.json"
REVIEW_LOG = "LAO_REVIEW.log"


def git_available() -> bool:
    return shutil.which("git") is not None


def git_wanted() -> bool:
    if not get_settings().git.enabled:
        return False
    if not git_available():
        log.warning("[Git] `git` not found on PATH; skipping VCS commits.")
        return False
    return True


def ensure_repo(root: Path) -> None:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        r = subprocess.run(
            ["git", "-C", str(root), "init"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or "").strip()
            log.warning("[Git] git init failed: %s", msg)
            raise RuntimeError("git init failed")
        log.info("[Git] Initialized repository in %s", root)
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(_MINIMAL_GITIGNORE, encoding="utf-8")


def _package_version() -> str:
    try:
        from local_ai_agent_orchestrator import __version__

        return __version__
    except Exception:
        return "unknown"


def write_plan_snapshot(root: Path, source_plan_filename: str, plan_text: str) -> Path:
    name = (get_settings().git.plan_file_name or "LAO_PLAN.md").strip() or "LAO_PLAN.md"
    path = root / name
    header = (
        f"<!-- LAO plan snapshot: source={source_plan_filename} "
        f"lao_version={_package_version()} -->\n\n"
    )
    path.write_text(header + plan_text, encoding="utf-8")
    return path


def _build_subject(agent: str, summary: str, max_total: int = 72) -> str:
    prefix = f"lao({agent}): "
    summary = re.sub(r"[\r\n]+", " ", summary.strip())
    room = max_total - len(prefix)
    if room < 8:
        room = 8
    if len(summary) > room:
        summary = summary[: room - 3].rstrip() + "..."
    return prefix + summary


def _merge_body(*parts: str | None) -> str | None:
    lines: list[str] = []
    for p in parts:
        if p and p.strip():
            lines.append(p.strip())
    return "\n".join(lines) if lines else None


def _trailers(plan_id: str | None = None, task_id: int | None = None) -> str | None:
    if not get_settings().git.commit_trailers:
        return None
    lines = []
    if plan_id:
        lines.append(f"LAO-Plan-ID: {plan_id}")
    if task_id is not None:
        lines.append(f"LAO-Task-ID: {task_id}")
    return "\n".join(lines) if lines else None


def commit_all(root: Path, subject: str, body: str | None = None) -> bool:
    """
    Stage all changes and commit if there is anything staged.
    Returns True if a commit was created.
    """
    global _identity_warned
    root = root.resolve()
    r = subprocess.run(
        ["git", "-C", str(root), "add", "-A"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        log.warning("[Git] git add failed: %s", (r.stderr or r.stdout or "").strip())
        return False

    r = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--quiet"])
    if r.returncode == 0:
        return False

    cmd = ["git", "-C", str(root), "commit", "-m", subject]
    merged = _merge_body(body)
    if merged:
        cmd.extend(["-m", merged])

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        log.warning("[Git] commit failed: %s", err)
        el = err.lower()
        if ("tell me who you are" in el or "user.name" in el) and not _identity_warned:
            log.warning(
                "[Git] Set commit identity: git config user.email and user.name "
                "(global or in this repo)."
            )
            _identity_warned = True
        return False

    log.info("[Git] %s", subject)
    return True


def write_tasks_artifact(root: Path, queue: TaskQueue, plan_id: str) -> None:
    tasks = queue.get_plan_tasks(plan_id)
    payload = {
        "plan_id": plan_id,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
            }
            for t in tasks
        ],
    }
    (root / TASKS_ARTIFACT).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def append_review_log(root: Path, task_id: int, verdict: str) -> None:
    line = (
        f"{datetime.now(timezone.utc).isoformat()} "
        f"task_id={task_id} verdict={verdict}\n"
    )
    path = root / REVIEW_LOG
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def snapshot_and_commit_plan(
    root: Path,
    plan_stem: str,
    source_plan_filename: str,
    plan_text: str,
    plan_id: str | None = None,
) -> None:
    if not git_wanted():
        return
    try:
        ensure_repo(root)
        write_plan_snapshot(root, source_plan_filename, plan_text)
        subj = _build_subject("plan", f"add plan snapshot for {plan_stem}")
        first = plan_text.strip().split("\n", 1)[0].strip()[:200]
        body = _merge_body(
            first if first else None,
            _trailers(plan_id=plan_id),
        )
        commit_all(root, subj, body)
    except Exception as e:
        log.warning("[Git] plan snapshot skipped: %s", e)


def commit_after_architect(
    root: Path,
    queue: TaskQueue,
    plan_id: str,
    plan_stem: str,
    n_tasks: int,
) -> None:
    if not git_wanted():
        return
    try:
        ensure_repo(root)
        write_tasks_artifact(root, queue, plan_id)
        subj = _build_subject(
            "architect",
            f"decompose plan {plan_stem} into {n_tasks} tasks",
        )
        body = _merge_body(f"Plan-ID: {plan_id}", _trailers(plan_id=plan_id))
        commit_all(root, subj, body)
    except Exception as e:
        log.warning("[Git] architect commit skipped: %s", e)


def commit_after_coder(root: Path, plan_id: str, task_id: int, title: str) -> None:
    if not git_wanted():
        return
    try:
        ensure_repo(root)
        subj = _build_subject("coder", f"task #{task_id} {title}")
        body = _merge_body(f"Plan-ID: {plan_id}", _trailers(plan_id=plan_id, task_id=task_id))
        commit_all(root, subj, body)
    except Exception as e:
        log.warning("[Git] coder commit skipped: %s", e)


def commit_after_reviewer(
    root: Path,
    plan_id: str,
    task_id: int,
    title: str,
    verdict: str,
) -> None:
    """
    verdict: approved | rejected | failed
    """
    if not git_wanted():
        return
    try:
        ensure_repo(root)
        if verdict == "approved":
            summary = f"approved task #{task_id} {title}"
        elif verdict == "rejected":
            summary = f"rejected task #{task_id} (rework) {title}"
        else:
            summary = f"failed task #{task_id} (max attempts) {title}"
        subj = _build_subject("reviewer", summary)
        append_review_log(root, task_id, verdict)
        body = _merge_body(
            f"Plan-ID: {plan_id}",
            _trailers(plan_id=plan_id, task_id=task_id),
        )
        commit_all(root, subj, body)
    except Exception as e:
        log.warning("[Git] reviewer commit skipped: %s", e)
