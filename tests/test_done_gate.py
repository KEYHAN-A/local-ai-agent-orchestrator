# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the DONE-gate skeleton: acceptance runner + plan-level gate."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.done_gate import evaluate_plan_done
from local_ai_agent_orchestrator.services.acceptance import (
    run_plan_acceptance,
    run_task_acceptance,
)
from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue


class _GateTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        reset_settings_for_tests()
        init_settings(cwd=self.root)
        self.db_path = self.root / "state.db"
        self.queue = TaskQueue(self.db_path)

    def tearDown(self) -> None:
        self.queue.close()
        reset_settings_for_tests()
        self._tmp.cleanup()

    def _register_plan_with_tasks(self, tasks: list[dict]) -> str:
        plan_id = self.queue.register_plan("plan.md", "# plan\n" + str(tasks))
        self.queue.add_tasks(plan_id, tasks)
        return plan_id


class TestAcceptanceRunner(_GateTestBase):
    def test_skipped_when_no_commands(self):
        plan_id = self._register_plan_with_tasks([
            {"title": "T1", "description": "d", "file_paths": [], "dependencies": []},
        ])
        task = self.queue.get_plan_tasks(plan_id)[0]
        result = run_task_acceptance(self.queue, task, self.root)
        self.assertTrue(result["skipped"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["runs"], [])

    def test_runs_passing_command(self):
        plan_id = self._register_plan_with_tasks([
            {
                "title": "T1",
                "description": "d",
                "file_paths": [],
                "dependencies": [],
                "acceptance": {
                    "commands": ["true"],
                    "acceptance_ids": ["AC-1"],
                    "tests": [],
                },
            },
        ])
        task = self.queue.get_plan_tasks(plan_id)[0]
        self.assertEqual(task.acceptance["commands"], ["true"])
        result = run_task_acceptance(self.queue, task, self.root)
        self.assertFalse(result["skipped"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["acceptance_ids"], ["AC-1"])
        runs = self.queue.get_validation_runs(task.id)
        kinds = [r["kind"] for r in runs]
        self.assertIn("acceptance", kinds)

    def test_failing_command_marks_failure(self):
        plan_id = self._register_plan_with_tasks([
            {
                "title": "T1",
                "description": "d",
                "file_paths": [],
                "dependencies": [],
                "acceptance": {"commands": ["false"], "acceptance_ids": ["AC-1"]},
            },
        ])
        task = self.queue.get_plan_tasks(plan_id)[0]
        result = run_task_acceptance(self.queue, task, self.root)
        self.assertFalse(result["passed"])
        self.assertEqual(result["runs"][0]["return_code"], 1)


class TestPlanDoneGateLegacy(_GateTestBase):
    def test_legacy_plan_passes_when_all_completed(self):
        plan_id = self._register_plan_with_tasks([
            {"title": "T1", "description": "d", "file_paths": [], "dependencies": []},
            {"title": "T2", "description": "d", "file_paths": [], "dependencies": []},
        ])
        for t in self.queue.get_plan_tasks(plan_id):
            self.queue.mark_completed(t.id)

        report = evaluate_plan_done(self.queue, plan_id, self.root)
        self.assertTrue(report["plan_done"], msg=report)
        self.assertEqual(report["task_breakdown"].get("completed"), 2)
        self.assertEqual(report["ac_coverage"]["declared"], 0)

    def test_legacy_plan_fails_when_task_pending(self):
        plan_id = self._register_plan_with_tasks([
            {"title": "T1", "description": "d", "file_paths": [], "dependencies": []},
            {"title": "T2", "description": "d", "file_paths": [], "dependencies": []},
        ])
        tasks = self.queue.get_plan_tasks(plan_id)
        self.queue.mark_completed(tasks[0].id)

        report = evaluate_plan_done(self.queue, plan_id, self.root)
        self.assertFalse(report["plan_done"])
        self.assertTrue(any("not completed" in r for r in report["reasons"]))


class TestPlanDoneGateAcceptance(_GateTestBase):
    def test_passes_when_acceptance_green(self):
        plan_id = self._register_plan_with_tasks([
            {
                "title": "T1",
                "description": "d",
                "file_paths": [],
                "dependencies": [],
                "acceptance": {"commands": ["true"], "acceptance_ids": ["AC-1"]},
            },
        ])
        for t in self.queue.get_plan_tasks(plan_id):
            self.queue.mark_completed(t.id)

        report = evaluate_plan_done(self.queue, plan_id, self.root, run_acceptance=True)
        self.assertTrue(report["plan_done"], msg=report)
        self.assertEqual(report["ac_coverage"]["passing"], 1)
        self.assertEqual(report["ac_coverage"]["missing"], [])

    def test_fails_when_acceptance_red(self):
        plan_id = self._register_plan_with_tasks([
            {
                "title": "T1",
                "description": "d",
                "file_paths": [],
                "dependencies": [],
                "acceptance": {"commands": ["false"], "acceptance_ids": ["AC-1"]},
            },
        ])
        for t in self.queue.get_plan_tasks(plan_id):
            self.queue.mark_completed(t.id)

        report = evaluate_plan_done(self.queue, plan_id, self.root, run_acceptance=True)
        self.assertFalse(report["plan_done"])
        self.assertTrue(any("acceptance" in r for r in report["reasons"]))
        self.assertIn("AC-1", report["ac_coverage"]["missing"])

    def test_fails_when_critical_finding_present(self):
        plan_id = self._register_plan_with_tasks([
            {
                "title": "T1",
                "description": "d",
                "file_paths": [],
                "dependencies": [],
                "acceptance": {"commands": ["true"], "acceptance_ids": ["AC-1"]},
            },
        ])
        task = self.queue.get_plan_tasks(plan_id)[0]
        self.queue.mark_completed(task.id)
        self.queue.add_finding(
            task.id,
            source="reviewer",
            severity="critical",
            issue_class="logic",
            message="boom",
        )

        report = evaluate_plan_done(self.queue, plan_id, self.root, run_acceptance=True)
        self.assertFalse(report["plan_done"])
        self.assertTrue(any("critical" in r for r in report["reasons"]))

    def test_fails_on_blocking_open_question(self):
        plan_id = self._register_plan_with_tasks([
            {
                "title": "T1",
                "description": "d",
                "file_paths": [],
                "dependencies": [],
                "acceptance": {"commands": ["true"], "acceptance_ids": ["AC-1"]},
            },
        ])
        for t in self.queue.get_plan_tasks(plan_id):
            self.queue.mark_completed(t.id)
        spec = self.root / "SPEC.md"
        spec.write_text(
            "# SPEC\n\n## Open questions\n- BLOCKING Should we support X?\n",
            encoding="utf-8",
        )

        report = evaluate_plan_done(
            self.queue, plan_id, self.root, run_acceptance=True, spec_doc_path=spec
        )
        self.assertFalse(report["plan_done"])
        self.assertTrue(any("BLOCKING" in r for r in report["reasons"]))

    def test_critic_rejected_blocks_done(self):
        plan_id = self._register_plan_with_tasks([
            {
                "title": "T1",
                "description": "d",
                "file_paths": [],
                "dependencies": [],
                "acceptance": {"commands": ["true"], "acceptance_ids": ["AC-1"]},
            },
        ])
        task = self.queue.get_plan_tasks(plan_id)[0]
        self.queue.mark_completed(task.id)
        self.queue.set_task_critic_votes(task.id, {"verdict": "rejected", "votes": []})

        report = evaluate_plan_done(self.queue, plan_id, self.root, run_acceptance=True)
        self.assertFalse(report["plan_done"])
        self.assertEqual(report["critic"]["rejected"], 1)


class TestRunPlanAcceptance(_GateTestBase):
    def test_runs_only_completed_tasks(self):
        plan_id = self._register_plan_with_tasks([
            {
                "title": "T1",
                "description": "d",
                "file_paths": [],
                "dependencies": [],
                "acceptance": {"commands": ["true"]},
            },
            {
                "title": "T2",
                "description": "d",
                "file_paths": [],
                "dependencies": [],
                "acceptance": {"commands": ["false"]},
            },
        ])
        tasks = self.queue.get_plan_tasks(plan_id)
        self.queue.mark_completed(tasks[0].id)

        report = run_plan_acceptance(self.queue, plan_id, self.root, only_completed=True)
        self.assertTrue(report["passed"])
        self.assertEqual(report["evaluated_count"], 1)


class TestPersistedAcceptance(_GateTestBase):
    def test_round_trip(self):
        plan_id = self._register_plan_with_tasks([
            {"title": "T1", "description": "d", "file_paths": [], "dependencies": []},
        ])
        task = self.queue.get_plan_tasks(plan_id)[0]
        self.queue.set_task_acceptance(
            task.id, {"commands": ["pytest -q tests/x.py"], "acceptance_ids": ["AC-9"]}
        )
        loaded = self.queue.get_task_acceptance(task.id)
        self.assertEqual(loaded["acceptance_ids"], ["AC-9"])
        self.assertEqual(self.queue.get_plan_tasks(plan_id)[0].acceptance["acceptance_ids"], ["AC-9"])
        self.queue.set_task_acceptance(task.id, None)
        self.assertIsNone(self.queue.get_task_acceptance(task.id))


if __name__ == "__main__":
    unittest.main()
