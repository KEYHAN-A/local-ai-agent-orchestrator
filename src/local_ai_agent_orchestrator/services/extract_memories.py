# SPDX-License-Identifier: GPL-3.0-or-later
"""
Extract durable facts after a successful reviewer approval.

Heuristics-only (no extra LLM call) so the extraction stays fast and
deterministic. Captures three buckets:

1. ``Files written: ...`` claims become ``Wrote <path>`` facts.
2. Build / lint commands that succeeded validation become ``Build cmd: ...``.
3. The reviewer summary's leading sentence (if any) becomes a "Decision: ..."
   fact when it contains decision keywords.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from local_ai_agent_orchestrator.services import memory
from local_ai_agent_orchestrator.state import MicroTask, TaskQueue

log = logging.getLogger(__name__)


_DECISION_KEYWORDS = ("decided", "chose", "selected", "use ", "introduce", "adopted")
_FILES_WRITTEN_RE = re.compile(r"Files\s+written:\s*([^\n]+)", re.IGNORECASE)


def _facts_from_coder_output(task: MicroTask, output: str) -> Iterable[str]:
    if not output:
        return ()
    facts: list[str] = []
    m = _FILES_WRITTEN_RE.search(output)
    if m:
        files = [p.strip().strip("`,") for p in m.group(1).split(",") if p.strip()]
        for fp in files[:6]:
            facts.append(f"Wrote {fp} (task: {task.title})")
    return facts


def _facts_from_validation(queue: TaskQueue, task: MicroTask) -> Iterable[str]:
    facts: list[str] = []
    try:
        runs = queue.get_validation_runs(task.id)
    except Exception:
        return facts
    for r in runs:
        if r.get("success") and r.get("command"):
            kind = (r.get("kind") or "validation").strip()
            cmd = (r.get("command") or "").strip()
            if cmd:
                facts.append(f"{kind.capitalize()} cmd: {cmd}")
    return facts


def _facts_from_summary(summary: str) -> Iterable[str]:
    if not summary:
        return ()
    line = summary.strip().splitlines()[0].strip(" .-*")
    lowered = line.lower()
    if any(k in lowered for k in _DECISION_KEYWORDS):
        return [f"Decision: {line[:200]}"]
    return ()


def extract_for_task(
    queue: TaskQueue,
    task: MicroTask,
    coder_output: str,
    reviewer_summary: str,
) -> int:
    """Extract and persist facts for *task*; returns the number newly stored."""
    candidates: list[str] = []
    candidates.extend(_facts_from_coder_output(task, coder_output))
    candidates.extend(_facts_from_validation(queue, task))
    candidates.extend(_facts_from_summary(reviewer_summary))

    added = 0
    source = f"task#{task.id}"
    for fact in candidates:
        if memory.append_fact(fact, scope="project", source=source):
            try:
                queue.add_memory_fact("project", fact, source=source)
            except Exception:
                pass
            added += 1
    if added:
        log.info(f"[Memory] +{added} fact(s) from task #{task.id}")
    return added
