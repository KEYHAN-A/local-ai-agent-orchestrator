# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for operator dashboard snapshot generation."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.dashboards import (
    build_dashboard_snapshot,
    write_dashboard_snapshot,
)
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue


MINIMAL_YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
paths:
  plans: ./plans
  database: ./.lao/state.db
"""


class TestDashboards(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_dashboard_snapshot_contains_required_sections(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            pid = q.register_plan("Plan.md", "REQ-1")
            q.upsert_deliverables(pid, [{"id": "REQ-1", "description": "deliver"}])
            q.set_deliverable_status(pid, "REQ-1", "blocked", reason="dependency missing")
            q.add_tasks(
                pid,
                [{"title": "T1", "description": "desc", "file_paths": [], "dependencies": []}],
            )
            t = q.get_plan_tasks(pid)[0]
            q.mark_rework(t.id, "retry needed")
            q.log_run(
                task_id=t.id,
                phase="reviewer",
                model_key="reviewer-model",
                success=False,
                error="failed",
            )
            payload = build_dashboard_snapshot(q)
            self.assertIn("blocked_deliverables", payload)
            self.assertIn("retry_loops", payload)
            self.assertIn("failure_classes_by_model_phase", payload)
            self.assertIn("summary", payload)
            self.assertIn("deltas", payload)
            self.assertIn("top_failure_classes", payload)
            self.assertIn("top_new_failure_classes", payload)
            self.assertIn("worsened_failure_classes", payload)
            self.assertIn("improved_failure_classes", payload)
            self.assertIn("regression_hints", payload)
            self.assertIn("run_events_total", payload["summary"])
            self.assertIn("failure_rate_delta", payload["deltas"])
            self.assertTrue(payload["blocked_deliverables"])
            self.assertTrue(payload["retry_loops"])
            out = write_dashboard_snapshot(root, payload)
            self.assertTrue(out.exists())
            q.close()

    def test_dashboard_deltas_compare_with_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            pid = q.register_plan("Plan.md", "REQ-1")
            q.upsert_deliverables(pid, [{"id": "REQ-1", "description": "deliver"}])
            previous = {
                "summary": {
                    "blocked_deliverables_count": 0,
                    "retry_loops_count": 0,
                    "failure_events_total": 0,
                },
                "failure_classes_by_model_phase": {},
            }
            q.set_deliverable_status(pid, "REQ-1", "blocked", reason="dependency missing")
            q.add_tasks(
                pid,
                [{"title": "T1", "description": "desc", "file_paths": [], "dependencies": []}],
            )
            t = q.get_plan_tasks(pid)[0]
            q.mark_rework(t.id, "retry needed")
            q.log_run(task_id=t.id, phase="reviewer", model_key="m", success=False, error="failed")
            payload = build_dashboard_snapshot(q, previous=previous)
            self.assertGreater(payload["deltas"]["blocked_deliverables_delta"], 0)
            self.assertGreater(payload["deltas"]["retry_loops_delta"], 0)
            self.assertGreaterEqual(payload["deltas"]["failure_rate_delta"], 0.0)
            self.assertTrue(payload["worsened_failure_classes"])
            self.assertTrue(payload["top_new_failure_classes"])
            self.assertTrue(payload["regression_hints"])
            q.close()


if __name__ == "__main__":
    unittest.main()

