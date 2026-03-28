"""Operator dashboard snapshot generators."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from local_ai_agent_orchestrator.state import TaskQueue


def build_dashboard_snapshot(queue: TaskQueue, previous: dict | None = None) -> dict:
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

    current = {
        "blocked_deliverables": blocked_deliverables,
        "retry_loops": retry_loops,
        "failure_classes_by_model_phase": failure_classes_by_model_phase,
    }
    current["summary"] = {
        "blocked_deliverables_count": len(blocked_deliverables),
        "retry_loops_count": len(retry_loops),
        "failure_events_total": sum(int(v) for v in failure_classes_by_model_phase.values()),
        "run_events_total": len(run_rows),
    }
    current["deltas"] = _compute_deltas(current, previous or {})
    current["top_failure_classes"] = _top_failure_classes(failure_classes_by_model_phase)
    current["top_new_failure_classes"] = _top_new_failure_classes(current["deltas"].get("failure_class_deltas", {}))
    current["worsened_failure_classes"] = sorted(
        [k for k, v in current["deltas"].get("failure_class_deltas", {}).items() if int(v) > 0]
    )
    current["improved_failure_classes"] = sorted(
        [k for k, v in current["deltas"].get("failure_class_deltas", {}).items() if int(v) < 0]
    )
    current["regression_hints"] = _build_regression_hints(current["deltas"], current["top_failure_classes"])
    return current


def write_dashboard_snapshot(workspace: Path, payload: dict) -> Path:
    out = workspace / "dashboard_snapshot.json"
    tmp_fd, tmp_path = tempfile.mkstemp(dir=workspace, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp_path, out)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return out


def _compute_deltas(current: dict, previous: dict) -> dict:
    cur_summary = current.get("summary", {}) if isinstance(current, dict) else {}
    prev_summary = previous.get("summary", {}) if isinstance(previous, dict) else {}
    cur_fail = current.get("failure_classes_by_model_phase", {}) if isinstance(current, dict) else {}
    prev_fail = previous.get("failure_classes_by_model_phase", {}) if isinstance(previous, dict) else {}
    all_keys = sorted(set(cur_fail.keys()) | set(prev_fail.keys()))
    failure_deltas = {
        k: int(cur_fail.get(k, 0)) - int(prev_fail.get(k, 0))
        for k in all_keys
        if int(cur_fail.get(k, 0)) - int(prev_fail.get(k, 0)) != 0
    }
    return {
        "blocked_deliverables_delta": int(cur_summary.get("blocked_deliverables_count", 0))
        - int(prev_summary.get("blocked_deliverables_count", 0)),
        "retry_loops_delta": int(cur_summary.get("retry_loops_count", 0))
        - int(prev_summary.get("retry_loops_count", 0)),
        "failure_events_delta": int(cur_summary.get("failure_events_total", 0))
        - int(prev_summary.get("failure_events_total", 0)),
        "failure_rate_delta": _rate(
            int(cur_summary.get("failure_events_total", 0)),
            int(cur_summary.get("run_events_total", 0)),
        )
        - _rate(
            int(prev_summary.get("failure_events_total", 0)),
            int(prev_summary.get("run_events_total", 0)),
        ),
        "failure_class_deltas": failure_deltas,
    }


def _top_failure_classes(rows: dict[str, int], limit: int = 3) -> list[dict]:
    ranked = sorted(rows.items(), key=lambda kv: (-int(kv[1]), kv[0]))
    return [{"class": k, "count": int(v)} for k, v in ranked[: max(1, limit)]]


def _top_new_failure_classes(deltas: dict[str, int], limit: int = 3) -> list[dict]:
    ranked = sorted(
        [(k, int(v)) for k, v in deltas.items() if int(v) > 0],
        key=lambda kv: (-kv[1], kv[0]),
    )
    return [{"class": k, "delta": v} for k, v in ranked[: max(1, limit)]]


def _build_regression_hints(deltas: dict, top_failures: list[dict]) -> list[str]:
    out: list[str] = []
    if int(deltas.get("blocked_deliverables_delta", 0)) > 0:
        out.append("Blocked deliverables increased; inspect dependency and deferral reasons.")
    if int(deltas.get("retry_loops_delta", 0)) > 0:
        out.append("Retry loops increased; review repeated failure signatures and cooldown policy.")
    if int(deltas.get("failure_events_delta", 0)) > 0:
        out.append("Failure events increased; inspect top model|phase failure classes.")
    if float(deltas.get("failure_rate_delta", 0.0)) > 0:
        out.append("Failure rate per run event worsened; investigate model/phase reliability shifts.")
    if top_failures:
        top = ", ".join(f"{r['class']}={r['count']}" for r in top_failures)
        out.append(f"Top failure classes: {top}")
    return out


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)

