"""Quality report generation utilities."""

from __future__ import annotations

import json
from pathlib import Path

from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import TaskQueue
from local_ai_agent_orchestrator.validators import infer_plan_languages


def write_quality_report(
    queue: TaskQueue, plan_id: str, model_metrics: dict[str, int] | None = None
) -> Path:
    ws = queue.workspace_for_plan(plan_id)
    tasks = queue.get_plan_tasks(plan_id)
    findings = {t.id: queue.get_findings(t.id) for t in tasks}
    validations = {t.id: queue.get_validation_runs(t.id) for t in tasks}
    completed = sum(1 for t in tasks if t.status == "completed")
    failed = sum(1 for t in tasks if t.status == "failed")
    pending = sum(1 for t in tasks if t.status in ("pending", "coding", "coded", "review"))

    traceability = []
    for t in tasks:
        traceability.append(
            {
                "task_id": t.id,
                "title": t.title,
                "file_paths": t.file_paths,
                "phase_name": t.phase_name,
                "deliverable_ids": t.deliverable_ids,
                "status": t.status,
                "dependencies": t.dependencies,
                "findings_count": len(findings.get(t.id, [])),
                "validation_runs": validations.get(t.id, []),
            }
        )
    deliverables = queue.get_deliverables(plan_id)
    strict_adherence = bool(get_settings().strict_adherence)
    strict_allowed = {
        str(x).strip().lower()
        for x in (get_settings().strict_closure_allowed_statuses or ["validated"])
        if str(x).strip()
    } or {"validated"}
    preflight = queue.get_plan_preflight(plan_id) or {}
    total_deliverables = len(deliverables)
    validated_deliverables = sum(1 for d in deliverables if d.get("status") == "validated")
    unresolved_deliverables = [
        d for d in deliverables if str(d.get("status", "")).lower() != "validated"
    ]
    alignment_score = (validated_deliverables / total_deliverables) if total_deliverables else 1.0
    rework_loops = sum(int(t.attempt) for t in tasks)

    payload = {
        "plan_id": plan_id,
        "detected_languages": sorted(infer_plan_languages(ws)),
        "task_counts": {
            "total": len(tasks),
            "completed": completed,
            "failed": failed,
            "pending_or_inflight": pending,
        },
        "quality": {
            "total_findings": sum(len(v) for v in findings.values()),
            "critical_findings": sum(
                1 for rows in findings.values() for f in rows if f.get("severity") == "critical"
            ),
            "consistency_findings": sum(
                1
                for rows in findings.values()
                for f in rows
                if str(f.get("issue_class", "")).startswith("referenced_")
                or str(f.get("issue_class", "")).endswith("_mismatch")
            ),
        },
        "preflight": preflight,
        "contracts": {
            "total_validation_runs": sum(len(v) for v in validations.values()),
            "failed_validation_runs": sum(
                1
                for runs in validations.values()
                for r in runs
                if not bool(r.get("success"))
            ),
            "strict_adherence_enabled": strict_adherence,
            "strict_closure_allowed_statuses": sorted(strict_allowed),
            "closure_satisfied": queue.is_plan_closure_satisfied(
                plan_id,
                strict_adherence=strict_adherence,
                allowed_statuses=strict_allowed,
            ),
            "unresolved_deliverables": unresolved_deliverables,
        },
        "traceability_summary": {
            "deliverables_total": total_deliverables,
            "deliverables_validated": validated_deliverables,
            "architecture_alignment_score": round(alignment_score, 4),
        },
        "convergence": {
            "rework_loops": rework_loops,
            "tasks_with_retries": sum(1 for t in tasks if int(t.attempt) > 0),
        },
        "deliverables": deliverables,
        "efficiency": {
            **queue.get_efficiency_metrics(),
            **(model_metrics or {}),
        },
        "traceability": traceability,
    }
    out = ws / "quality_report.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out
