"""Benchmark scenarios for orchestration reliability checks."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from local_ai_agent_orchestrator.phases import _parse_architect_output, preflight_plan_context
from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.validators import (
    validate_cross_file_consistency,
    validate_files,
)


def run_benchmark_suite() -> dict:
    s = get_settings()
    planner = s.models["planner"]
    results = {
        "large_plan_preflight": _benchmark_large_plan_preflight(
            planner.context_length, planner.max_completion
        ),
        "malformed_architect_output": _benchmark_malformed_architect_output(),
        "synthetic_artifact_detection": _benchmark_synthetic_artifact_detection(),
        "missing_symbol_leakage": _benchmark_missing_symbol_leakage(),
    }
    passed = sum(1 for v in results.values() if bool(v.get("passed")))
    return {
        "suite": "core_reliability",
        "passed": passed,
        "total": len(results),
        "results": results,
    }


def write_benchmark_report(workspace: Path, payload: dict) -> Path:
    out = workspace / "benchmark_report.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def _benchmark_large_plan_preflight(context_length: int, max_completion: int) -> dict:
    # Build a long synthetic plan to force chunking behavior.
    block = "\n".join(f"- Task item {i}: implement detail" for i in range(1, 450))
    plan_text = "# Phase 1\n" + block + "\n# Phase 2\n" + block
    pf = preflight_plan_context(plan_text, context_length, max_completion)
    passed = bool(pf.get("chunk_count", 0) >= 1 and "fallback_chain" in pf)
    return {
        "passed": passed,
        "chunk_count": int(pf.get("chunk_count", 0)),
        "fit": bool(pf.get("fit")),
    }


def _benchmark_malformed_architect_output() -> dict:
    malformed = '{"title":"x"}'
    try:
        _parse_architect_output(malformed)
        return {"passed": False, "error": "Malformed architect output unexpectedly parsed"}
    except Exception:
        return {"passed": True}


def _benchmark_synthetic_artifact_detection() -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ws = root / "Graph.workspace"
        ws.write_text("...\n", encoding="utf-8")
        findings = validate_files(root, ["Graph.workspace"])
        hit = any(f.issue_class == "synthetic_project_graph" for f in findings)
        return {"passed": hit, "findings": len(findings)}


def _benchmark_missing_symbol_leakage() -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "app.py").write_text("def real_func():\n    return 1\n", encoding="utf-8")
        (root / "test_app.py").write_text(
            "from app import MissingClass\n\nclass TestX:\n    pass\n",
            encoding="utf-8",
        )
        findings = validate_cross_file_consistency(root, {"python"})
        hit = any(f.issue_class == "test_symbol_mismatch" for f in findings)
        return {"passed": hit, "findings": len(findings)}

