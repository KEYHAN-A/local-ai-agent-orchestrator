# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for dependency-aware scheduling and plan chunk persistence."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue


MINIMAL_YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
paths:
  plans: ./plans
  database: ./.lao/state.db
"""


class TestSchedulerAndChunks(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_dependency_aware_next_pending(self):
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
                    {"title": "B", "description": "b", "file_paths": [], "dependencies": ["A"]},
                ],
            )
            first = q.next_pending()
            self.assertIsNotNone(first)
            self.assertEqual(first.title, "A")
            q.mark_completed(first.id)
            second = q.next_pending()
            self.assertIsNotNone(second)
            self.assertEqual(second.title, "B")
            q.close()

    def test_plan_chunk_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            pid = q.register_plan("Plan.md", "x")
            q.upsert_plan_chunk(pid, 0, "chunk-0")
            q.mark_plan_chunk_done(
                pid,
                0,
                [{"title": "T", "description": "d", "file_paths": [], "dependencies": []}],
            )
            chunks = q.get_plan_chunks(pid)
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0]["status"], "completed")
            self.assertEqual(chunks[0]["tasks"][0]["title"], "T")
            q.close()


if __name__ == "__main__":
    unittest.main()
