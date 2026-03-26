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

    def test_strict_plan_closure_requires_validated_deliverables(self):
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
                [{"title": "A", "description": "a", "file_paths": [], "dependencies": []}],
            )
            t = q.get_plan_tasks(pid)[0]
            q.mark_completed(t.id)
            self.assertTrue(q.is_plan_terminal(pid))
            self.assertFalse(q.is_plan_closure_satisfied(pid, strict_adherence=True))
            q.set_deliverable_status(pid, "REQ-1", "validated")
            self.assertTrue(q.is_plan_closure_satisfied(pid, strict_adherence=True))
            q.close()

    def test_strict_closure_allows_policy_statuses(self):
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
                [{"title": "A", "description": "a", "file_paths": [], "dependencies": []}],
            )
            t = q.get_plan_tasks(pid)[0]
            q.mark_completed(t.id)
            q.set_deliverable_status(pid, "REQ-1", "deferred", reason="approved waiver")
            self.assertFalse(q.is_plan_closure_satisfied(pid, strict_adherence=True))
            self.assertTrue(
                q.is_plan_closure_satisfied(
                    pid, strict_adherence=True, allowed_statuses={"validated", "deferred"}
                )
            )
            q.close()

    def test_validation_run_records_lifecycle_fields(self):
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
                [{"title": "A", "description": "a", "file_paths": [], "dependencies": []}],
            )
            t = q.get_plan_tasks(pid)[0]
            q.add_validation_run(
                t.id,
                kind="command:build",
                success=False,
                command="make build",
                output="failed",
                status="completed",
                return_code=2,
                started_at="2026-01-01T00:00:00+00:00",
                finished_at="2026-01-01T00:00:05+00:00",
            )
            runs = q.get_validation_runs(t.id)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["return_code"], 2)
            self.assertEqual(runs[0]["status"], "completed")
            self.assertEqual(runs[0]["command"], "make build")
            q.close()

    def test_deliverable_status_requires_reason_for_blocked_states(self):
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
            with self.assertRaises(ValueError):
                q.set_deliverable_status(pid, "REQ-1", "blocked")
            q.set_deliverable_status(pid, "REQ-1", "blocked", reason="Dependency missing")
            rows = q.get_deliverables(pid)
            self.assertEqual(rows[0]["status"], "blocked")
            self.assertEqual(rows[0]["status_reason"], "Dependency missing")
            q.close()


if __name__ == "__main__":
    unittest.main()
