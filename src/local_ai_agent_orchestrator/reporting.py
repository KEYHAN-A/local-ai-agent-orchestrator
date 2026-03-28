"""Quality report generation utilities."""

from __future__ import annotations

import json
from pathlib import Path

from local_ai_agent_orchestrator.report_schema import build_report_meta
from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import TaskQueue
from local_ai_agent_orchestrator.validators import infer_plan_languages, infer_validation_commands


def _write_lao_quality_markdown(workspace: Path, payload: dict) -> Path:
    """Human-readable summary next to quality_report.json."""
    tc = payload.get("task_counts", {})
    q = payload.get("quality", {})
    tr = payload.get("traceability_summary", {})
    contracts = payload.get("contracts", {})
    vi = payload.get("validation_inference") or {}
    lines = [
        "# LAO quality report",
        "",
        "## Summary",
        f"- **Plan** `{payload.get('plan_id', '')}`",
        f"- **Tasks** — completed: {tc.get('completed', 0)}, failed: {tc.get('failed', 0)}, "
        f"pending/in-flight: {tc.get('pending_or_inflight', 0)} (total {tc.get('total', 0)})",
        f"- **Findings** — total: {q.get('total_findings', 0)}, critical: {q.get('critical_findings', 0)}",
        f"- **Deliverables** — validated {tr.get('deliverables_validated', 0)} / "
        f"{tr.get('deliverables_total', 0)} (alignment score {tr.get('architecture_alignment_score', 0)})",
        f"- **Validation runs** — failed: {contracts.get('failed_validation_runs', 0)} / "
        f"{contracts.get('total_validation_runs', 0)}",
        f"- **Strict closure** — enabled: {contracts.get('strict_adherence_enabled', False)}, "
        f"satisfied: {contracts.get('closure_satisfied', False)}",
        "",
        "## Inferred validation commands",
        f"- Manifest inference enabled: {vi.get('enabled', False)}",
        f"- Suggested build: `{vi.get('suggested_build') or '—'}`",
        f"- Suggested lint: `{vi.get('suggested_lint') or '—'}`",
        "",
        "_Machine-readable data: `quality_report.json` in this folder._",
        "",
    ]
    out = workspace / "LAO_QUALITY.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _load_json_if_exists(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


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
                "next_eligible_at": t.next_eligible_at,
                "escalation_reason": t.escalation_reason,
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
    escalated = [t for t in tasks if (t.escalation_reason or "").strip()]
    escalation_counts: dict[str, int] = {}
    for t in escalated:
        key = (t.escalation_reason or "").strip()
        escalation_counts[key] = escalation_counts.get(key, 0) + 1
    analyzer_confidence: dict[str, list[float]] = {}
    for rows in findings.values():
        for f in rows:
            key = str(f.get("analyzer_kind") or "unknown")
            try:
                val = float(f.get("confidence"))
            except Exception:
                continue
            analyzer_confidence.setdefault(key, []).append(val)
    analyzer_confidence_summary = {
        k: {
            "count": len(v),
            "avg_confidence": round(sum(v) / len(v), 4) if v else 0.0,
        }
        for k, v in sorted(analyzer_confidence.items())
    }
    benchmark_payload = _load_json_if_exists(ws / "benchmark_report.json")
    kpi_payload = _load_json_if_exists(ws / "kpi_snapshot.json")
    dashboard_payload = _load_json_if_exists(ws / "dashboard_snapshot.json")
    observability = {
        "benchmark_gate": (
            {
                "gate_passed": bool(benchmark_payload.get("gate", {}).get("gate_passed", False)),
                "pass_rate": benchmark_payload.get("pass_rate"),
                "gate_reasons": benchmark_payload.get("gate", {}).get("gate_reasons", []),
            }
            if benchmark_payload
            else None
        ),
        "kpi_snapshot_ref": (
            {
                "plans_total": kpi_payload.get("plans_total"),
                "plan_success_rate": kpi_payload.get("plan_success_rate"),
            }
            if kpi_payload
            else None
        ),
        "dashboard_regression_summary": (
            {
                "failure_events_delta": dashboard_payload.get("deltas", {}).get("failure_events_delta"),
                "failure_rate_delta": dashboard_payload.get("deltas", {}).get("failure_rate_delta"),
                "regression_hints": dashboard_payload.get("regression_hints", []),
            }
            if dashboard_payload
            else None
        ),
    }

    plan_langs = infer_plan_languages(ws)
    settings = get_settings()
    ib, il = infer_validation_commands(ws, plan_langs)
    validation_inference = {
        "enabled": bool(settings.infer_validation_commands),
        "suggested_build": ib,
        "suggested_lint": il,
    }

    payload = {
        "report_meta": build_report_meta(),
        "plan_id": plan_id,
        "detected_languages": sorted(plan_langs),
        "validation_inference": validation_inference,
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
            "analyzer_confidence_summary": analyzer_confidence_summary,
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
        "observability": observability,
        "convergence": {
            "rework_loops": rework_loops,
            "tasks_with_retries": sum(1 for t in tasks if int(t.attempt) > 0),
            "escalated_tasks": len(escalated),
            "escalation_reason_counts": escalation_counts,
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
    _write_lao_quality_markdown(ws, payload)
    return out
