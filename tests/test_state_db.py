# SPDX-License-Identifier: GPL-3.0-or-later
"""SQLite path wiring for TaskQueue."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import ReservedPlanStemError, TaskQueue


MINIMAL_YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
paths:
  plans: ./plans
  database: ./.lao/state.db
"""


class TestTaskQueueDatabasePath(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_default_connects_to_settings_db_not_literal_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")

            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            self.assertTrue(q.db_path.name.endswith(".db"))
            self.assertTrue(q.db_path.exists())
            stray = root / "None"
            self.assertFalse(stray.exists())
            q.close()


class TestReservedPlanStem(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_register_rejects_reserved_stem(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            with self.assertRaises(ReservedPlanStemError):
                q.register_plan("plans.md", "x")
            q.close()


class TestTaskQueueStateHelpers(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_reset_failed_tasks_and_terminal_detection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            pid = q.register_plan("Plan.md", "x")
            q.add_tasks(
                pid,
                [
                    {"title": "A", "description": "a", "file_paths": [], "dependencies": []},
                    {"title": "B", "description": "b", "file_paths": [], "dependencies": []},
                ],
            )
            tasks = q.get_plan_tasks(pid)
            q.mark_completed(tasks[0].id)
            q.mark_failed(tasks[1].id, "failed for test")
            self.assertTrue(q.is_plan_terminal(pid))

            reset_count = q.reset_failed_tasks(plan_id=pid)
            self.assertEqual(reset_count, 1)
            self.assertFalse(q.is_plan_terminal(pid))
            q.close()


if __name__ == "__main__":
    unittest.main()
