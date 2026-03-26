# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for KPI snapshot generation."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.kpi import build_kpi_snapshot, write_kpi_snapshot
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue


MINIMAL_YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
orchestration:
  strict_adherence: true
paths:
  plans: ./plans
  database: ./.lao/state.db
"""


class TestKpi(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_kpi_snapshot_build_and_write(self):
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
            q.add_tasks(
                pid,
                [
                    {
                        "title": "T1",
                        "description": "desc",
                        "file_paths": ["a.py"],
                        "dependencies": [],
                        "deliverable_ids": ["REQ-1"],
                    }
                ],
            )
            t = q.get_plan_tasks(pid)[0]
            q.mark_completed(t.id)
            q.set_deliverable_status(pid, "REQ-1", "validated")
            q.add_validation_run(t.id, kind="build", success=True, command="echo ok", output="ok")
            snap = build_kpi_snapshot(q)
            self.assertIn("plan_success_rate", snap)
            self.assertIn("first_pass_validation_success", snap)
            out = write_kpi_snapshot(root, snap)
            self.assertTrue(out.exists())
            q.close()


if __name__ == "__main__":
    unittest.main()

