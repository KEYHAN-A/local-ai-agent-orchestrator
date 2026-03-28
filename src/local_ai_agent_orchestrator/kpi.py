"""KPI snapshot utilities for weekly reliability tracking."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from statistics import median

from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.state import TaskQueue


def build_kpi_snapshot(queue: TaskQueue) -> dict:
    plans = queue.get_plans()
    all_tasks = []
    deliverables_total = 0
    deliverables_mapped = 0
    deliverables_validated_with_runs = 0
    cross_file_leakage = 0
    for p in plans:
        tasks = queue.get_plan_tasks(p["id"])
        all_tasks.extend(tasks)
        dels = queue.get_deliverables(p["id"])
        deliverables_total += len(dels)
        for d in dels:
            did = str(d.get("deliverable_id", "")).strip()
            mapped_tasks = [t for t in tasks if did and did in (t.deliverable_ids or [])]
            if mapped_tasks:
                deliverables_mapped += 1
                if any(queue.get_validation_runs(t.id) for t in mapped_tasks):
                    deliverables_validated_with_runs += 1
        for t in tasks:
            for f in queue.get_findings(t.id):
                ic = str(f.get("issue_class", ""))
                if ic.startswith("referenced_") or ic.endswith("_mismatch"):
                    cross_file_leakage += 1

    strict = bool(get_settings().strict_adherence)
    allowed = {
        str(x).strip().lower()
        for x in (get_settings().strict_closure_allowed_statuses or ["validated"])
        if str(x).strip()
    } or {"validated"}
    successful_plans = sum(
        1
        for p in plans
        if queue.is_plan_closure_satisfied(
            p["id"], strict_adherence=strict, allowed_statuses=allowed
        )
    )
    completed = [t for t in all_tasks if t.status == "completed"]
    first_pass = [t for t in completed if int(t.attempt) == 0]
    retries = [int(t.attempt) for t in completed]
    failed_escape = 0
    for t in completed:
        rows = queue.get_findings(t.id)
        if any(str(f.get("severity", "")).lower() in {"critical", "major"} for f in rows):
            failed_escape += 1

    tokens = queue.get_total_tokens()
    validated_deliverables = 0
    for p in plans:
        validated_deliverables += sum(
            1 for d in queue.get_deliverables(p["id"]) if str(d.get("status")) == "validated"
        )
    token_eff = (tokens["prompt_tokens"] + tokens["completion_tokens"]) / max(
        1, validated_deliverables
    )

    return {
        "plans_total": len(plans),
        "plan_success_rate": round(successful_plans / max(1, len(plans)), 4),
        "first_pass_validation_success": round(len(first_pass) / max(1, len(completed)), 4),
        "rework_convergence_median_retries": float(median(retries)) if retries else 0.0,
        "contract_fail_escape_rate": round(failed_escape / max(1, len(completed)), 4),
        "traceability_coverage": round(deliverables_mapped / max(1, deliverables_total), 4),
        "traceability_validated_with_runs": round(
            deliverables_validated_with_runs / max(1, deliverables_total), 4
        ),
        "cross_file_defect_leakage": cross_file_leakage,
        "token_efficiency_per_validated_deliverable": round(token_eff, 2),
    }


def write_kpi_snapshot(workspace: Path, payload: dict) -> Path:
    out = workspace / "kpi_snapshot.json"
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

