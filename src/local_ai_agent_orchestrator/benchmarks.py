"""Benchmark scenarios for orchestration reliability checks."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from local_ai_agent_orchestrator.phases import _parse_architect_output, preflight_plan_context
from local_ai_agent_orchestrator.settings import get_settings
from local_ai_agent_orchestrator.validators import (
    validate_cross_file_consistency,
    validate_files,
)

SCENARIO_THRESHOLDS: dict[str, dict] = {
    "large_plan_preflight_small": {
        "max_duration_ms": 4000.0,
        "require_chunking_signal": True,
    },
    "large_plan_preflight_large": {
        "max_duration_ms": 6000.0,
        "require_chunking_signal": True,
    },
    "malformed_architect_output_object": {
        "max_duration_ms": 1000.0,
    },
    "malformed_architect_output_bad_json": {
        "max_duration_ms": 1000.0,
    },
    "synthetic_artifact_detection": {
        "min_findings": 1,
        "max_duration_ms": 2000.0,
    },
    "missing_symbol_leakage": {
        "min_findings": 1,
        "max_duration_ms": 2000.0,
    },
    "typescript_syntax_detection": {
        "min_findings": 1,
        "max_duration_ms": 2000.0,
    },
}


def run_benchmark_suite(previous: dict | None = None) -> dict:
    s = get_settings()
    planner = s.models["planner"]
    scenarios = [
        ("large_plan_preflight_small", lambda: _benchmark_large_plan_preflight(planner.context_length, planner.max_completion, blocks=180)),
        ("large_plan_preflight_large", lambda: _benchmark_large_plan_preflight(planner.context_length, planner.max_completion, blocks=500)),
        ("malformed_architect_output_object", _benchmark_malformed_architect_output_object),
        ("malformed_architect_output_bad_json", _benchmark_malformed_architect_output_bad_json),
        ("synthetic_artifact_detection", _benchmark_synthetic_artifact_detection),
        ("missing_symbol_leakage", _benchmark_missing_symbol_leakage),
        ("typescript_syntax_detection", _benchmark_typescript_syntax_detection),
    ]
    results = {}
    for name, fn in scenarios:
        t0 = time.perf_counter()
        row = fn()
        row["duration_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        row.setdefault("failure_class", None if row.get("passed") else "scenario_failure")
        row.setdefault("regression_hint", "Inspect scenario output and compare with previous baseline.")
        threshold_violations = _evaluate_scenario_thresholds(name, row)
        row["threshold_violations"] = threshold_violations
        if threshold_violations:
            row["passed"] = False
            if not row.get("failure_class"):
                row["failure_class"] = "scenario_threshold_violation"
        results[name] = row
    passed = sum(1 for v in results.values() if bool(v.get("passed")))
    total = len(results)
    pass_rate = passed / max(1, total)
    prev_map = (previous or {}).get("results", {}) if isinstance(previous, dict) else {}
    regressions = []
    for name, row in results.items():
        prev_row = prev_map.get(name, {}) if isinstance(prev_map, dict) else {}
        if prev_row and bool(prev_row.get("passed")) and not bool(row.get("passed")):
            regressions.append(name)
    gate_ok = (pass_rate >= float(s.benchmark_min_pass_rate)) and (
        (not bool(s.benchmark_fail_on_regression)) or (len(regressions) == 0)
    )
    gate_reasons: list[str] = []
    if pass_rate < float(s.benchmark_min_pass_rate):
        gate_reasons.append(
            f"pass_rate_below_min:{round(pass_rate, 4)}<{float(s.benchmark_min_pass_rate):.4f}"
        )
    if bool(s.benchmark_fail_on_regression) and regressions:
        gate_reasons.append(f"regressions_detected:{','.join(regressions)}")
    scenario_threshold_failures = sorted(
        [name for name, row in results.items() if row.get("threshold_violations")]
    )
    if scenario_threshold_failures:
        gate_reasons.append(f"scenario_threshold_failures:{','.join(scenario_threshold_failures)}")
        gate_ok = False
    return {
        "suite": "core_reliability",
        "passed": passed,
        "total": total,
        "pass_rate": round(pass_rate, 4),
        "gate": {
            "min_pass_rate": float(s.benchmark_min_pass_rate),
            "fail_on_regression": bool(s.benchmark_fail_on_regression),
            "regressions": regressions,
            "gate_reasons": gate_reasons,
            "gate_passed": gate_ok,
        },
        "results": results,
    }


def write_benchmark_report(workspace: Path, payload: dict) -> Path:
    out = workspace / "benchmark_report.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def _benchmark_large_plan_preflight(context_length: int, max_completion: int, blocks: int) -> dict:
    # Build a long synthetic plan to force chunking behavior.
    block = "\n".join(f"- Task item {i}: implement detail" for i in range(1, blocks))
    plan_text = "# Phase 1\n" + block + "\n# Phase 2\n" + block
    pf = preflight_plan_context(plan_text, context_length, max_completion)
    passed = bool(pf.get("chunk_count", 0) >= 1 and "fallback_chain" in pf)
    return {
        "passed": passed,
        "chunk_count": int(pf.get("chunk_count", 0)),
        "fit": bool(pf.get("fit")),
        "fallback_chain": list(pf.get("fallback_chain", [])),
        "failure_class": None if passed else "preflight_policy",
        "regression_hint": "Check preflight chunking policy and fallback chain contract.",
    }


def _benchmark_malformed_architect_output_object() -> dict:
    malformed = '{"title":"x"}'
    try:
        _parse_architect_output(malformed)
        return {
            "passed": False,
            "error": "Malformed architect object unexpectedly parsed",
            "failure_class": "architect_schema_strictness",
            "regression_hint": "Architect parser should reject non-array outputs.",
        }
    except Exception:
        return {"passed": True}


def _benchmark_malformed_architect_output_bad_json() -> dict:
    malformed = "[{bad json}]"
    try:
        _parse_architect_output(malformed)
        return {
            "passed": False,
            "error": "Malformed architect JSON unexpectedly parsed",
            "failure_class": "architect_json_parsing",
            "regression_hint": "Architect parser should reject invalid JSON syntax.",
        }
    except Exception:
        return {"passed": True}


def _benchmark_synthetic_artifact_detection() -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ws = root / "Graph.workspace"
        ws.write_text("...\n", encoding="utf-8")
        findings = validate_files(root, ["Graph.workspace"])
        hit = any(f.issue_class == "synthetic_project_graph" for f in findings)
        return {
            "passed": hit,
            "findings": len(findings),
            "failure_class": None if hit else "synthetic_artifact_detection",
            "regression_hint": "Ensure synthetic project graph checks are active for workspace artifacts.",
        }


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
        return {
            "passed": hit,
            "findings": len(findings),
            "failure_class": None if hit else "symbol_leakage_detection",
            "regression_hint": "Strengthen cross-file symbol validation for tests vs production code.",
        }


def _benchmark_typescript_syntax_detection() -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = root / "bad.ts"
        p.write_text("export const x = { foo: [1,2;\n", encoding="utf-8")
        findings = validate_files(root, ["bad.ts"])
        hit = any(f.issue_class == "typescript_unbalanced_delimiters" for f in findings)
        return {
            "passed": hit,
            "findings": len(findings),
            "failure_class": None if hit else "typescript_analyzer_coverage",
            "regression_hint": "Verify TypeScript analyzer registry and structural checks are active.",
        }


def _evaluate_scenario_thresholds(name: str, row: dict) -> list[str]:
    thresholds = SCENARIO_THRESHOLDS.get(name, {})
    if not thresholds:
        return []
    violations: list[str] = []
    max_duration_ms = thresholds.get("max_duration_ms")
    if max_duration_ms is not None and float(row.get("duration_ms", 0.0)) > float(max_duration_ms):
        violations.append(
            f"duration_ms_exceeded:{float(row.get('duration_ms', 0.0)):.2f}>{float(max_duration_ms):.2f}"
        )
    min_findings = thresholds.get("min_findings")
    if min_findings is not None and int(row.get("findings", 0)) < int(min_findings):
        violations.append(f"findings_below_min:{int(row.get('findings', 0))}<{int(min_findings)}")
    if bool(thresholds.get("require_chunking_signal")):
        if int(row.get("chunk_count", 0)) < 1:
            violations.append("chunk_count_missing")
        if "fallback_chain" not in row:
            violations.append("fallback_chain_missing")
    return violations

