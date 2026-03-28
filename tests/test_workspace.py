# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for per-plan workspaces and .lao layout."""

import tempfile
import unittest
from pathlib import Path

from local_ai_agent_orchestrator.settings import init_settings, reset_settings_for_tests
from local_ai_agent_orchestrator.state import TaskQueue
from local_ai_agent_orchestrator.tools import (
    _workspace_root,
    pick_pilot_tools_workspace,
    use_plan_workspace,
)


MINIMAL_YAML = """
lm_studio_base_url: "http://127.0.0.1:1234"
openai_api_key: "lm-studio"
paths:
  plans: ./plans
  database: ./.lao/state.db
"""


class TestPerPlanWorkspace(unittest.TestCase):
    def tearDown(self):
        reset_settings_for_tests()

    def test_workspace_for_plan_stem(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")

            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            pid = q.register_plan("IOS_DEV_PLAN.md", "hello plan body unique")
            expected = (root / "IOS_DEV_PLAN").resolve()
            self.assertEqual(q.workspace_for_plan(pid), expected)
            self.assertTrue(expected.is_dir())

    def test_use_plan_workspace_context(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")

            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            pid = q.register_plan("Foo.md", "other content")
            path = q.workspace_for_plan(pid)
            with use_plan_workspace(q, pid):
                self.assertEqual(_workspace_root(), path)

    def test_pick_pilot_tools_workspace_uses_newest_actionable_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            old = q.register_plan("Old.md", "a")
            new = q.register_plan("New.md", "b")
            q.add_tasks(old, [
                {"title": "O1", "description": "", "file_paths": [], "dependencies": []},
            ])
            q.add_tasks(new, [
                {"title": "N1", "description": "", "file_paths": [], "dependencies": []},
            ])
            t_old = q.next_pending()
            q.mark_completed(t_old.id)
            self.assertEqual(pick_pilot_tools_workspace(q), q.workspace_for_plan(new))
            q.close()

    def test_pick_pilot_tools_workspace_falls_back_to_config_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lao").mkdir(parents=True, exist_ok=True)
            (root / "plans").mkdir(parents=True, exist_ok=True)
            cfg = root / "factory.yaml"
            cfg.write_text(MINIMAL_YAML.strip(), encoding="utf-8")
            init_settings(config_path=cfg, cwd=root)
            q = TaskQueue()
            self.assertEqual(pick_pilot_tools_workspace(q), root.resolve())
            q.close()


if __name__ == "__main__":
    unittest.main()
