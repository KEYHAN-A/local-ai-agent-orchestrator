"""Operator dashboard snapshot generators."""

from __future__ import annotations

import json
from pathlib import Path

from local_ai_agent_orchestrator.state import TaskQueue


def build_dashboard_snapshot(queue: TaskQueue) -> dict:
    plans = queue.get_plans()
    blocked_deliverables: list[dict] = []
    retry_loops: list[dict] = []
    for p in plans:
        plan_id = p["id"]
        for d in queue.get_deliverables(plan_id):
            if str(d.get("status", "")).lower() in {"blocked", "deferred", "failed", "partial"}:
                blocked_deliverables.append(
                    {
                        "plan_id": plan_id,
                        "deliverable_id": d.get("deliverable_id"),
                        "status": d.get("status"),
                        "reason": d.get("status_reason"),
                        "updated_at": d.get("updated_at"),
                    }
                )
        for t in queue.get_plan_tasks(plan_id):
            if int(t.attempt) > 0 or (t.escalation_reason or "").strip():
                retry_loops.append(
                    {
                        "plan_id": plan_id,
                        "task_id": t.id,
                        "title": t.title,
                        "phase_name": t.phase_name,
                        "attempt": int(t.attempt),
                        "status": t.status,
                        "next_eligible_at": t.next_eligible_at,
                        "escalation_reason": t.escalation_reason,
                    }
                )

    run_rows = queue.get_run_log_entries()
    failure_classes_by_model_phase: dict[str, int] = {}
    for r in run_rows:
        if bool(r.get("success")):
            continue
        key = f"{r.get('model_key','unknown')}|{r.get('phase','unknown')}"
        failure_classes_by_model_phase[key] = failure_classes_by_model_phase.get(key, 0) + 1

    return {
        "blocked_deliverables": blocked_deliverables,
        "retry_loops": retry_loops,
        "failure_classes_by_model_phase": failure_classes_by_model_phase,
    }


def write_dashboard_snapshot(workspace: Path, payload: dict) -> Path:
    out = workspace / "dashboard_snapshot.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out

