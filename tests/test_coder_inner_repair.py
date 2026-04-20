# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the TDD inner-repair loop in coder_phase."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_ai_agent_orchestrator.phases import _coder_inner_repair_loop
from local_ai_agent_orchestrator.settings import (
    get_settings,
    init_settings,
    reset_settings_for_tests,
)
from local_ai_agent_orchestrator.state import TaskQueue


def _make_task(queue: TaskQueue, *, with_acceptance: bool = True):
    plan_id = queue.register_plan("p.md", "x")
    queue.add_tasks(plan_id, [{
        "title": "T1",
        "description": "implement",
        "file_paths": ["src/x.py"],
        "dependencies": [],
        "acceptance": {
            "acceptance_ids": ["AC-1"],
            "commands": ["true"],  # placeholder; tests override via mock
        } if with_acceptance else {},
    }])
    return plan_id, queue.get_plan_tasks(plan_id)[0]


class _FakeCfg:
    supports_tools = False
    max_completion = 512


class TestInnerRepair(unittest.TestCase):
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

    def test_returns_none_when_no_acceptance(self):
        plan_id, task = _make_task(self.queue, with_acceptance=False)
        result = _coder_inner_repair_loop(
            client=mock.Mock(),
            queue=self.queue,
            task=task,
            workspace=self.root,
            model_key="fake",
            cfg=_FakeCfg(),
        )
        self.assertIsNone(result)

    def test_returns_none_when_max_iter_zero(self):
        plan_id, task = _make_task(self.queue)
        s = get_settings()
        from dataclasses import replace
        from local_ai_agent_orchestrator.settings import _settings as _, init_settings  # noqa: F401
        # Force max_iter=0 via init_settings overrides
        reset_settings_for_tests()
        init_settings(cwd=self.root, inner_repair_max_iterations=0)
        result = _coder_inner_repair_loop(
            client=mock.Mock(), queue=self.queue, task=task,
            workspace=self.root, model_key="fake", cfg=_FakeCfg(),
        )
        self.assertIsNone(result)

    def test_passes_immediately_does_not_call_coder(self):
        plan_id, task = _make_task(self.queue)
        ws = self.queue.workspace_for_plan(plan_id)

        passing = {"task_id": task.id, "passed": True, "skipped": False,
                   "runs": [{"command": "true", "return_code": 0, "output": "", "passed": True}],
                   "tests": [], "acceptance_ids": ["AC-1"]}

        with mock.patch(
            "local_ai_agent_orchestrator.services.acceptance.run_task_acceptance",
            return_value=passing,
        ) as run_mock, mock.patch(
            "local_ai_agent_orchestrator.phases._coder_no_tools"
        ) as coder_mock:
            summary = _coder_inner_repair_loop(
                client=mock.Mock(), queue=self.queue, task=task,
                workspace=ws, model_key="fake", cfg=_FakeCfg(),
            )

        self.assertIn("GREEN", summary)
        coder_mock.assert_not_called()
        run_mock.assert_called_once()

    def test_repairs_then_passes(self):
        plan_id, task = _make_task(self.queue)
        ws = self.queue.workspace_for_plan(plan_id)

        results = [
            {"task_id": task.id, "passed": False, "skipped": False,
             "runs": [{"command": "false", "return_code": 1, "output": "boom", "passed": False}],
             "tests": [], "acceptance_ids": ["AC-1"]},
            {"task_id": task.id, "passed": True, "skipped": False,
             "runs": [{"command": "true", "return_code": 0, "output": "ok", "passed": True}],
             "tests": [], "acceptance_ids": ["AC-1"]},
        ]
        rt = mock.MagicMock(side_effect=results)
        with mock.patch(
            "local_ai_agent_orchestrator.services.acceptance.run_task_acceptance", rt
        ), mock.patch(
            "local_ai_agent_orchestrator.phases._coder_no_tools",
            return_value="patched",
        ) as coder_mock:
            summary = _coder_inner_repair_loop(
                client=mock.Mock(), queue=self.queue, task=task,
                workspace=ws, model_key="fake", cfg=_FakeCfg(),
            )

        self.assertIn("GREEN", summary)
        self.assertEqual(coder_mock.call_count, 1)
        self.assertEqual(self.queue.get_inner_repairs(task.id), 1)

    def test_caps_iterations(self):
        reset_settings_for_tests()
        init_settings(cwd=self.root, inner_repair_max_iterations=2)
        plan_id, task = _make_task(self.queue)
        ws = self.queue.workspace_for_plan(plan_id)

        always_red = {"task_id": task.id, "passed": False, "skipped": False,
                      "runs": [{"command": "false", "return_code": 1, "output": "x", "passed": False}],
                      "tests": [], "acceptance_ids": []}
        with mock.patch(
            "local_ai_agent_orchestrator.services.acceptance.run_task_acceptance",
            return_value=always_red,
        ), mock.patch(
            "local_ai_agent_orchestrator.phases._coder_no_tools",
            return_value="x",
        ) as coder_mock:
            summary = _coder_inner_repair_loop(
                client=mock.Mock(), queue=self.queue, task=task,
                workspace=ws, model_key="fake", cfg=_FakeCfg(),
            )

        self.assertIn("RED", summary)
        self.assertEqual(coder_mock.call_count, 2)
        self.assertEqual(self.queue.get_inner_repairs(task.id), 2)


if __name__ == "__main__":
    unittest.main()
