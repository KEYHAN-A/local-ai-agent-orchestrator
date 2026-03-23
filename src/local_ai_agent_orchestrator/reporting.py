"""Quality report generation utilities."""

from __future__ import annotations

import json
from pathlib import Path

from local_ai_agent_orchestrator.state import TaskQueue
from local_ai_agent_orchestrator.validators import infer_plan_languages


def write_quality_report(
    queue: TaskQueue, plan_id: str, model_metrics: dict[str, int] | None = None
) -> Path:
    ws = queue.workspace_for_plan(plan_id)
    tasks = queue.get_plan_tasks(plan_id)
    findings = {t.id: queue.get_findings(t.id) for t in tasks}
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
                "status": t.status,
                "dependencies": t.dependencies,
                "findings_count": len(findings.get(t.id, [])),
            }
        )

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
        },
        "efficiency": {
            **queue.get_efficiency_metrics(),
            **(model_metrics or {}),
        },
        "traceability": traceability,
    }
    out = ws / "quality_report.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out
