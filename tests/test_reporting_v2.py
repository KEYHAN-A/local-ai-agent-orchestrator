# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for quality report v2 fields."""

import json
import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.reporting import write_quality_report
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue


MINIMAL_YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
orchestration:
  strict_adherence: true
  strict_closure_allowed_statuses: [validated, deferred]
paths:
  plans: ./plans
  database: ./.lao/state.db
"""


class TestReportingV2(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_report_contains_preflight_contracts_traceability(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            pid = q.register_plan("Plan.md", "REQ-1 implement")
            q.set_plan_preflight(pid, {"fit": False, "chunk_count": 2})
            q.upsert_deliverables(pid, [{"id": "REQ-1", "description": "do it"}])
            q.add_tasks(
                pid,
                [
                    {
                        "title": "T1",
                        "description": "desc",
                        "file_paths": ["a.py"],
                        "dependencies": [],
                        "deliverable_ids": ["REQ-1"],
                        "phase": "Phase 1",
                    }
                ],
            )
            t = q.get_plan_tasks(pid)[0]
            q.add_validation_run(t.id, kind="build", success=False, command="x", output="bad")
            q.add_finding(
                t.id,
                source="validator",
                severity="major",
                issue_class="x",
                message="m",
                analyzer_id="schema_lints",
                analyzer_kind="heuristic",
                confidence=0.8,
            )
            q.mark_completed(t.id)
            q.set_deliverable_status(pid, "REQ-1", "validated")
            q.mark_failed(t.id, "boom", escalation_reason="reviewer_exception")
            ws = q.workspace_for_plan(pid)
            (ws / "benchmark_report.json").write_text(
                json.dumps(
                    {
                        "pass_rate": 0.86,
                        "gate": {"gate_passed": False, "gate_reasons": ["regressions_detected:x"]},
                    }
                ),
                encoding="utf-8",
            )
            (ws / "kpi_snapshot.json").write_text(
                json.dumps({"plans_total": 4, "plan_success_rate": 0.75}),
                encoding="utf-8",
            )
            (ws / "dashboard_snapshot.json").write_text(
                json.dumps(
                    {
                        "deltas": {"failure_events_delta": 2, "failure_rate_delta": 0.12},
                        "regression_hints": ["Failure events increased"],
                    }
                ),
                encoding="utf-8",
            )
            out = write_quality_report(q, pid)
            payload = json.loads(out.read_text(encoding="utf-8"))
            md_path = ws / "LAO_QUALITY.md"
            self.assertTrue(md_path.is_file())
            self.assertIn("LAO quality report", md_path.read_text(encoding="utf-8"))
            self.assertIn("validation_inference", payload)
            self.assertIn("suggested_build", payload["validation_inference"])
            self.assertIn("report_meta", payload)
            self.assertIn("schema_version", payload["report_meta"])
            self.assertIn("preflight", payload)
            self.assertIn("contracts", payload)
            self.assertIn("traceability_summary", payload)
            self.assertEqual(payload["traceability_summary"]["deliverables_validated"], 1)
            self.assertIn("closure_satisfied", payload["contracts"])
            self.assertEqual(payload["contracts"]["closure_satisfied"], True)
            self.assertEqual(
                payload["contracts"]["strict_closure_allowed_statuses"],
                ["deferred", "validated"],
            )
            self.assertIn("escalation_reason_counts", payload["convergence"])
            self.assertEqual(payload["convergence"]["escalation_reason_counts"]["reviewer_exception"], 1)
            self.assertIn("analyzer_confidence_summary", payload["quality"])
            self.assertEqual(payload["quality"]["analyzer_confidence_summary"]["heuristic"]["count"], 1)
            self.assertIn("observability", payload)
            self.assertIn("benchmark_gate", payload["observability"])
            self.assertEqual(payload["observability"]["benchmark_gate"]["gate_passed"], False)
            self.assertEqual(payload["observability"]["kpi_snapshot_ref"]["plans_total"], 4)
            self.assertEqual(
                payload["observability"]["dashboard_regression_summary"]["failure_events_delta"], 2
            )
            q.close()


if __name__ == "__main__":
    unittest.main()
