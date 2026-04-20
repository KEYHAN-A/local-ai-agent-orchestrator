# SPDX-License-Identifier: GPL-3.0-or-later
"""Plan Integrator phase.

After the DONE gate passes, the integrator:

1. Re-runs every task's acceptance commands (regression sweep across the whole
   plan, not just the last-edited task).
2. Computes the AC coverage report (declared vs passing acceptance IDs).
3. Appends a single ``decision log`` entry to project memory so future runs can
   recall *what* was shipped and *why* it was considered done.

The decision log itself lives at ``<config_dir>/.lao/decisions.jsonl`` (one
JSON record per line) AND is summarised as a single Markdown bullet appended
to the project memory file (``LAO_MEMORY.md``) so it shows up in the
system-prompt prelude on subsequent runs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from local_ai_agent_orchestrator.services import memory as _memory
from local_ai_agent_orchestrator.services.acceptance import run_plan_acceptance
from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import TaskQueue

log = logging.getLogger(__name__)

DECISION_LOG_FILENAME = "decisions.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def decision_log_path() -> Optional[Path]:
    """Return the project-scoped decision log file path (creating the dir)."""
    try:
        s = get_settings()
    except RuntimeError:
        return None
    target = (s.config_dir / ".lao" / DECISION_LOG_FILENAME).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def append_decision(record: dict) -> Optional[Path]:
    """Append *record* (with ``timestamp`` injected) to the decision log."""
    path = decision_log_path()
    if path is None:
        return None
    enriched = dict(record)
    enriched.setdefault("timestamp", _now_iso())
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(enriched, sort_keys=True) + "\n")
    return path


def read_decisions(limit: Optional[int] = None) -> list[dict]:
    """Read decisions back (most recent last). Bad lines are skipped."""
    path = decision_log_path()
    if path is None or not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if limit is not None:
        return out[-limit:]
    return out


def compute_ac_coverage(queue: TaskQueue, plan_id: str, plan_acceptance: dict) -> dict:
    """Compute declared vs passing AC ids for a plan.

    ``plan_acceptance`` is the dict returned by :func:`run_plan_acceptance`.
    """
    declared: set[str] = set()
    for t in queue.get_plan_tasks(plan_id):
        for ac in (isinstance(t.acceptance, dict) and t.acceptance.get("acceptance_ids") or []):
            ac = str(ac).strip()
            if ac:
                declared.add(ac)
    passing: set[str] = set()
    for tr in plan_acceptance.get("task_results") or []:
        if not tr.get("passed"):
            continue
        for ac in tr.get("acceptance_ids") or []:
            ac = str(ac).strip()
            if ac:
                passing.add(ac)
    missing = sorted(declared - passing)
    return {
        "declared": sorted(declared),
        "passing": sorted(passing),
        "missing": missing,
        "coverage_ratio": (len(declared & passing) / len(declared)) if declared else 1.0,
    }


def integrate_plan(
    queue: TaskQueue,
    plan_id: str,
    workspace: Path,
    *,
    write_decision_log: bool = True,
) -> dict:
    """Run the integrator for *plan_id* and persist a decision-log entry.

    Returns::

        {
          "plan_id": str,
          "regression": <run_plan_acceptance result>,
          "ac_coverage": <compute_ac_coverage result>,
          "decision_logged": bool,
        }
    """
    s = get_settings()
    if not getattr(s, "plan_integrator_enabled", True):
        log.info("[Integrator] disabled in settings; skipping plan %s", plan_id)
        return {"plan_id": plan_id, "skipped": True}

    regression = run_plan_acceptance(queue, plan_id, workspace, only_completed=True)
    coverage = compute_ac_coverage(queue, plan_id, regression)

    plan_meta: dict = {}
    for p in queue.get_plans():
        if p.get("id") == plan_id:
            plan_meta = p
            break

    record = {
        "plan_id": plan_id,
        "filename": plan_meta.get("filename"),
        "status": "completed",
        "regression_passed": bool(regression.get("passed")),
        "tasks_evaluated": regression.get("evaluated_count", 0),
        "tasks_skipped": regression.get("skipped_count", 0),
        "ac_coverage": coverage,
    }

    decision_logged = False
    if write_decision_log and getattr(s, "decision_log_enabled", True):
        try:
            append_decision(record)
            decision_logged = True
        except Exception as exc:  # pragma: no cover
            log.warning("[Integrator] decision log write failed: %s", exc)
        try:
            verdict = "passed" if record["regression_passed"] else "failed"
            cov = coverage.get("coverage_ratio") or 0.0
            summary = (
                f"DONE plan {plan_meta.get('filename') or plan_id} — regression {verdict}, "
                f"AC coverage {len(coverage['passing'])}/{len(coverage['declared'])} "
                f"({cov*100:.0f}%)"
            )
            _memory.append_fact(summary, scope="project", source="plan_integrator")
        except Exception as exc:  # pragma: no cover
            log.debug("[Integrator] memory append failed: %s", exc)

    log.info(
        "[Integrator] Plan %s integrated (regression=%s, AC %d/%d)",
        plan_id,
        "passed" if regression.get("passed") else "failed",
        len(coverage["passing"]),
        len(coverage["declared"]),
    )
    return {
        "plan_id": plan_id,
        "regression": regression,
        "ac_coverage": coverage,
        "decision_logged": decision_logged,
    }
