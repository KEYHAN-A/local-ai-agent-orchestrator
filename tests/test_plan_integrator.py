# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Plan Integrator + decision log."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.plan_integrator import (
    append_decision,
    compute_ac_coverage,
    decision_log_path,
    integrate_plan,
    read_decisions,
)
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue


class _IntegratorBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        reset_settings_for_tests()
        init_settings(cwd=self.root)
        self.queue = TaskQueue(self.root / "state.db")

    def tearDown(self):
        self.queue.close()
        reset_settings_for_tests()
        self._tmp.cleanup()


class TestDecisionLog(_IntegratorBase):
    def test_decision_log_path_exists(self):
        path = decision_log_path()
        self.assertIsNotNone(path)
        self.assertTrue(path.parent.exists())

    def test_append_and_read_decision(self):
        append_decision({"plan_id": "p1", "regression_passed": True})
        append_decision({"plan_id": "p2", "regression_passed": False})
        records = read_decisions()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["plan_id"], "p1")
        self.assertIn("timestamp", records[0])
        self.assertEqual(records[-1]["regression_passed"], False)

    def test_read_decisions_skips_bad_lines(self):
        path = decision_log_path()
        path.write_text(
            json.dumps({"plan_id": "p1"}) + "\nnot-json\n" + json.dumps({"plan_id": "p2"}) + "\n",
            encoding="utf-8",
        )
        records = read_decisions()
        self.assertEqual([r["plan_id"] for r in records], ["p1", "p2"])


class TestAcCoverage(_IntegratorBase):
    def _make_plan_with_ac(self, declared_per_task, completed_per_task):
        plan_id = self.queue.register_plan("p.md", "x")
        payloads = []
        for ids in declared_per_task:
            payloads.append({
                "title": f"T{len(payloads)}",
                "description": "d",
                "file_paths": [],
                "dependencies": [],
                "acceptance": {"acceptance_ids": ids},
            })
        self.queue.add_tasks(plan_id, payloads)
        tasks = self.queue.get_plan_tasks(plan_id)
        for task, completed in zip(tasks, completed_per_task):
            if completed:
                self.queue.mark_coded(task.id, "code", code_signature="sig")
                self.queue.mark_completed(task.id)
        return plan_id

    def test_compute_coverage_no_declared_returns_one(self):
        plan_id = self._make_plan_with_ac([[]], [True])
        cov = compute_ac_coverage(self.queue, plan_id, {"task_results": []})
        self.assertEqual(cov["declared"], [])
        self.assertEqual(cov["coverage_ratio"], 1.0)

    def test_compute_coverage_some_missing(self):
        plan_id = self._make_plan_with_ac(
            declared_per_task=[["AC-1", "AC-2"], ["AC-3"]],
            completed_per_task=[True, True],
        )
        plan_acc = {
            "task_results": [
                {"passed": True, "acceptance_ids": ["AC-1"]},
                {"passed": False, "acceptance_ids": ["AC-3"]},
            ],
        }
        cov = compute_ac_coverage(self.queue, plan_id, plan_acc)
        self.assertEqual(cov["declared"], ["AC-1", "AC-2", "AC-3"])
        self.assertEqual(cov["passing"], ["AC-1"])
        self.assertEqual(cov["missing"], ["AC-2", "AC-3"])
        self.assertAlmostEqual(cov["coverage_ratio"], 1 / 3, places=2)


class TestIntegratePlan(_IntegratorBase):
    def test_integrate_runs_acceptance_and_logs_decision(self):
        plan_id = self.queue.register_plan("p.md", "x")
        self.queue.add_tasks(plan_id, [{
            "title": "Echo",
            "description": "do nothing",
            "file_paths": [],
            "dependencies": [],
            "acceptance": {
                "acceptance_ids": ["AC-1"],
                "commands": ["true"],
            },
        }])
        task = self.queue.get_plan_tasks(plan_id)[0]
        self.queue.mark_coded(task.id, "code", code_signature="sig")
        self.queue.mark_completed(task.id)

        report = integrate_plan(self.queue, plan_id, self.root)
        self.assertTrue(report["regression"]["passed"])
        self.assertEqual(report["ac_coverage"]["passing"], ["AC-1"])
        self.assertTrue(report["decision_logged"])

        records = read_decisions()
        self.assertTrue(records)
        self.assertEqual(records[-1]["plan_id"], plan_id)
        self.assertTrue(records[-1]["regression_passed"])

    def test_disabled_returns_skipped(self):
        reset_settings_for_tests()
        init_settings(cwd=self.root, plan_integrator_enabled=False)
        self.queue = TaskQueue(self.root / "state.db")
        plan_id = self.queue.register_plan("p.md", "x")
        report = integrate_plan(self.queue, plan_id, self.root)
        self.assertTrue(report.get("skipped"))


if __name__ == "__main__":
    unittest.main()
