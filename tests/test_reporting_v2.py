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
            q.mark_completed(t.id)
            q.set_deliverable_status(pid, "REQ-1", "validated")
            out = write_quality_report(q, pid)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("preflight", payload)
            self.assertIn("contracts", payload)
            self.assertIn("traceability_summary", payload)
            self.assertEqual(payload["traceability_summary"]["deliverables_validated"], 1)
            self.assertIn("closure_satisfied", payload["contracts"])
            self.assertEqual(payload["contracts"]["closure_satisfied"], True)
            q.close()


if __name__ == "__main__":
    unittest.main()
