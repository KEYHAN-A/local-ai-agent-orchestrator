# SPDX-License-Identifier: GPL-3.0-or-later
"""Plan-level Definition of DONE evaluator.

Returns a structured report describing whether a plan satisfies LAO's
``Definition of DONE``::

    Plan DONE iff
      - every task is in {completed} (no failed/pending/coding/coded/review)
      - every task with an acceptance contract has all commands green
      - every task's declared acceptance_ids are referenced by at least one
        passing acceptance run
      - critic quorum (when present) approves with majority
      - findings: no critical, majors <= task.allowed_major
      - no BLOCKING open questions remain (spec doctor)

Legacy plans (no task has an ``acceptance_json``) degrade gracefully: the
gate only enforces ``every task completed`` so existing pipelines keep
working unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from local_ai_agent_orchestrator.state import TaskQueue

log = logging.getLogger(__name__)


def _task_has_acceptance(task) -> bool:
    payload = getattr(task, "acceptance", None)
    if not isinstance(payload, dict):
        return False
    cmds = payload.get("commands") or []
    if isinstance(cmds, str):
        cmds = [cmds]
    return any(str(c).strip() for c in cmds)


def _aggregate_critic_verdict(votes: dict | None) -> str | None:
    """Reduce a critic-quorum payload to ``approved`` / ``rejected`` / None.

    The payload is whatever ``critic_quorum_phase`` writes to the task; we
    only require an aggregate ``verdict`` field. When absent, return None
    so the gate does not penalise legacy plans.
    """
    if not isinstance(votes, dict):
        return None
    verdict = str(votes.get("verdict") or "").strip().lower()
    if verdict in ("approved", "rejected"):
        return verdict
    return None


def evaluate_plan_done(
    queue: TaskQueue,
    plan_id: str,
    workspace: Path,
    *,
    run_acceptance: bool = True,
    spec_doc_path: Optional[Path] = None,
) -> dict:
    """Evaluate the DONE gate for ``plan_id``.

    When ``run_acceptance`` is True, each task with a contract is re-run via
    :mod:`services.acceptance` (so this captures regressions). Pass False to
    only consult previously-recorded validation runs.
    """
    tasks = queue.get_plan_tasks(plan_id)
    if not tasks:
        return {
            "plan_done": False,
            "reasons": ["plan has no tasks"],
            "task_breakdown": {},
            "ac_coverage": {"declared": 0, "passing": 0, "missing": []},
            "critic": {"required": 0, "approved": 0, "rejected": 0},
            "open_questions": [],
        }

    breakdown: dict[str, int] = {}
    failed_titles: list[str] = []
    for t in tasks:
        breakdown[t.status] = breakdown.get(t.status, 0) + 1
        if t.status == "failed":
            failed_titles.append(t.title)
    not_done = sum(c for s, c in breakdown.items() if s != "completed")

    reasons: list[str] = []
    if not_done:
        reasons.append(
            "some tasks not completed: "
            + ", ".join(f"{s}={c}" for s, c in sorted(breakdown.items()) if s != "completed")
        )

    declared_ac: set[str] = set()
    for t in tasks:
        payload = t.acceptance or {}
        for ac in (payload.get("acceptance_ids") or []):
            ac = str(ac).strip()
            if ac:
                declared_ac.add(ac)

    passing_ac: set[str] = set()
    acceptance_failed: list[int] = []
    if run_acceptance and any(_task_has_acceptance(t) for t in tasks):
        from local_ai_agent_orchestrator.services.acceptance import run_task_acceptance

        for t in tasks:
            if not _task_has_acceptance(t):
                continue
            res = run_task_acceptance(queue, t, workspace, record=True)
            if res.get("passed"):
                for ac in (res.get("acceptance_ids") or []):
                    passing_ac.add(str(ac))
            else:
                acceptance_failed.append(t.id)
        if acceptance_failed:
            reasons.append(
                f"acceptance failures on {len(acceptance_failed)} task(s): "
                + ", ".join(f"#{tid}" for tid in acceptance_failed[:6])
                + ("…" if len(acceptance_failed) > 6 else "")
            )

    missing_ac = sorted(declared_ac - passing_ac) if declared_ac else []
    if missing_ac:
        reasons.append(f"acceptance criteria without passing test: {', '.join(missing_ac[:8])}")

    critic_required = 0
    critic_approved = 0
    critic_rejected = 0
    for t in tasks:
        votes = queue.get_task_critic_votes(t.id)
        verdict = _aggregate_critic_verdict(votes)
        if verdict is None:
            continue
        critic_required += 1
        if verdict == "approved":
            critic_approved += 1
        else:
            critic_rejected += 1
    if critic_rejected:
        reasons.append(f"critic quorum rejected {critic_rejected} task(s)")

    crit_findings = 0
    major_findings = 0
    allowed_major_total = 0
    for t in tasks:
        try:
            findings = queue.get_findings(t.id)
        except Exception:
            findings = []
        for f in findings:
            sev = str(f.get("severity") or "").lower()
            if sev == "critical":
                crit_findings += 1
            elif sev == "major":
                major_findings += 1
        allowed_major_total += int((t.acceptance or {}).get("allowed_major", 0) or 0)
    if crit_findings:
        reasons.append(f"{crit_findings} critical finding(s) outstanding")
    if major_findings > allowed_major_total:
        reasons.append(
            f"{major_findings} major finding(s) exceed allowed budget {allowed_major_total}"
        )

    open_questions: list[str] = []
    if spec_doc_path and spec_doc_path.exists():
        try:
            text = spec_doc_path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if "BLOCKING" in line and (
                    "?" in line or line.strip().startswith(("-", "*", "1.", "Q:"))
                ):
                    open_questions.append(line.strip())
        except Exception:  # pragma: no cover - best effort only
            pass
    if open_questions:
        reasons.append(f"{len(open_questions)} BLOCKING open question(s) remain")

    plan_done = (not_done == 0) and not reasons
    return {
        "plan_done": bool(plan_done),
        "reasons": reasons,
        "task_breakdown": breakdown,
        "ac_coverage": {
            "declared": len(declared_ac),
            "passing": len(declared_ac & passing_ac),
            "missing": missing_ac,
        },
        "critic": {
            "required": critic_required,
            "approved": critic_approved,
            "rejected": critic_rejected,
        },
        "findings": {
            "critical": crit_findings,
            "major": major_findings,
            "allowed_major_budget": allowed_major_total,
        },
        "open_questions": open_questions,
        "failed_tasks": failed_titles[:10],
    }
